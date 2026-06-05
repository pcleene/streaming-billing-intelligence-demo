"""Run Spark Structured Streaming for Option A (notebook cells §0–§3 through lag sink).

Equivalent to executing `ml/feature_pipeline_and_training.ipynb` through the lag-stream cell:
Delta sinks for 24h/7d windows, Mongo writeback foreachBatch, and lag → Delta.

Loads env from `backend/.env`. Resolve Mongo URI like the backend (`MONGODB_URL`) or notebook (`MONGO_URI`).

Runs independently of the FastAPI backend — start before/with `make simulator` or docker compose.

Atlas **MONGODB-X509**: converts `TLS_CERT_PATH` PEM to `~/.cache/acme-billing-mongo-x509.p12` and sets JVM keyStore
(`ACME_MONGO_KEYSTORE_PASSWORD`, default `changeit`). Repo paths with spaces use `~/.cache/acme-billing-delta` for checkpoints.

CLI: `make spark-stream` (from repo root, after `pip install pyspark delta-spark` in `.venv`).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    DoubleType,
    MapType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)
from pyspark.sql.window import Window

from delta import configure_spark_with_delta_pip

REPO_ROOT = Path(__file__).resolve().parents[2]


def _mongo_uri() -> str:
    return os.environ.get("MONGO_URI") or os.environ["MONGODB_URL"]


def _mongo_db() -> str:
    return os.environ.get("MONGO_DB") or os.environ.get("ACME_DB", "streaming_billing")


def _mongo_connection_cert_opts(writer_or_reader):
    """Apply `connection.uri` (client TLS via JVM keyStore — see `_spark_ssl_keystore_configs`)."""
    return writer_or_reader.option("connection.uri", _mongo_uri())


def _pem_to_pkcs12(pem_path: Path, p12_path: Path, password: str) -> None:
    pem_path = pem_path.expanduser().resolve()
    p12_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "openssl",
            "pkcs12",
            "-export",
            "-in",
            str(pem_path),
            "-out",
            str(p12_path),
            "-password",
            f"pass:{password}",
            "-name",
            "client",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _spark_ssl_keystore_configs(builder) -> SparkSession.Builder:
    """Atlas MONGODB-X509: Spark's JVM needs a PKCS12 keyStore (PEM options are ignored)."""
    cert = os.environ.get("TLS_CERT_PATH") or os.environ.get("MONGO_X509_PATH") or ""
    if not cert.strip():
        return builder
    pem_path = Path(cert).expanduser().resolve()
    if not pem_path.is_file():
        raise FileNotFoundError(f"TLS_CERT_PATH not found: {pem_path}")

    ks_pass = os.environ.get("ACME_MONGO_KEYSTORE_PASSWORD", "changeit")
    cache_dir = Path.home() / ".cache"
    p12_path = cache_dir / "acme-billing-mongo-x509.p12"
    try:
        need_refresh = not p12_path.is_file() or p12_path.stat().st_mtime < pem_path.stat().st_mtime
        if need_refresh:
            _pem_to_pkcs12(pem_path, p12_path, ks_pass)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"openssl pkcs12 export failed: {e.stderr}") from e

    java_opts = (
        f"-Djavax.net.ssl.keyStore={p12_path} "
        f"-Djavax.net.ssl.keyStorePassword={ks_pass} "
        "-Djavax.net.ssl.keyStoreType=PKCS12"
    )
    return (
        builder.config("spark.driver.extraJavaOptions", java_opts).config(
            "spark.executor.extraJavaOptions", java_opts
        )
    )


def main() -> None:
    load_dotenv(REPO_ROOT / "backend" / ".env")

    mongo_db = _mongo_db()

    # Mongo Spark connector builds java.net.URI from checkpoint paths; spaces break parsing.
    repo_delta = REPO_ROOT / "data" / "delta"
    default_delta = Path.home() / ".cache" / "acme-billing-delta"
    if os.environ.get("ACME_DELTA_ROOT"):
        delta_root = Path(os.environ["ACME_DELTA_ROOT"])
    elif " " in str(repo_delta.resolve()):
        delta_root = default_delta
        print(f"Note: repo path contains spaces — using checkpoint root {delta_root}")
    else:
        delta_root = repo_delta

    delta_root.mkdir(parents=True, exist_ok=True)

    feature_delta = str(delta_root / "features_offline")
    lag_delta = str(delta_root / "stream_lag")
    checkpoint_root = str(delta_root / "_checkpoints")

    host_hint = _mongo_uri().split("@")[-1].split("/")[0]
    print(f"Mongo host hint: {host_hint}")
    print(f"DB: {mongo_db}")
    print(f"Delta root: {delta_root}")

    # Delta's helper merges Maven coords; it replaces spark.jars.packages — pass Mongo here.
    mongo_pkg = "org.mongodb.spark:mongo-spark-connector_2.12:10.3.0"

    builder = (
        SparkSession.builder.appName("acme-billing-features-live")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .config("spark.sql.shuffle.partitions", "8")
        .config("spark.sql.session.timeZone", "Asia/Kuala_Lumpur")
        # Python 3.13 + Spark 3.5: Janino occasionally fails on huge whole-stage codegen for these plans.
        .config("spark.sql.codegen.wholeStage", "false")
    )
    builder = _spark_ssl_keystore_configs(builder)
    spark = configure_spark_with_delta_pip(builder, extra_packages=[mongo_pkg]).getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    print(f"Spark {spark.version} session ready.")

    txn_schema = StructType(
        [
            StructField("transaction_id", StringType(), False),
            StructField("customer_id", StringType(), False),
            StructField("merchant_id", StringType(), True),
            StructField("amount", DoubleType(), True),
            StructField("discount_amount", DoubleType(), True),
            StructField("timestamp", StringType(), True),
            StructField("location", MapType(StringType(), StringType()), True),
        ]
    )

    txn_stream = (
        _mongo_connection_cert_opts(spark.readStream.format("mongodb"))
        .option("database", mongo_db)
        .option("collection", "transactions")
        .option("mode", "PERMISSIVE")
        .option("change.stream.publish.full.document.only", "true")
        .option("change.stream.lookup.full.document", "updateLookup")
        .schema(txn_schema)
        .load()
        .withColumn("timestamp", F.col("timestamp").cast(TimestampType()))
        .where(F.col("amount").isNotNull() & F.col("timestamp").isNotNull())
        .withColumn("customer_bucket", F.abs(F.hash("customer_id")) % F.lit(32))
    )

    feature_24h = (
        txn_stream.withWatermark("timestamp", "30 minutes")
        .groupBy(
            F.window("timestamp", "24 hours", "5 minutes").alias("w"),
            "customer_id",
            "customer_bucket",
        )
        .agg(
            F.count("*").alias("txn_count_24h"),
            F.sum("amount").alias("spend_24h_myr"),
            F.sum("discount_amount").alias("discount_24h_myr"),
        )
        .select(
            "customer_id",
            "customer_bucket",
            F.col("w.end").alias("window_end"),
            "txn_count_24h",
            "spend_24h_myr",
            "discount_24h_myr",
        )
    )

    feature_7d = (
        txn_stream.withWatermark("timestamp", "1 hour")
        .groupBy(
            F.window("timestamp", "7 days", "30 minutes").alias("w"),
            "customer_id",
            "customer_bucket",
        )
        .agg(
            F.count("*").alias("txn_count_7d"),
            F.sum("amount").alias("spend_7d_myr"),
            (
                F.sum("discount_amount")
                / F.when(F.sum("amount") == 0, F.lit(None)).otherwise(F.sum("amount"))
            ).alias("discount_rate_7d"),
        )
        .select(
            "customer_id",
            "customer_bucket",
            F.col("w.end").alias("window_end"),
            "txn_count_7d",
            "spend_7d_myr",
            "discount_rate_7d",
        )
    )

    q_24h = (
        feature_24h.writeStream.format("delta")
        .outputMode("append")
        .partitionBy("customer_bucket")
        .option("checkpointLocation", f"{checkpoint_root}/feature_24h")
        .start(f"{feature_delta}/window_24h")
    )

    q_7d = (
        feature_7d.writeStream.format("delta")
        .outputMode("append")
        .partitionBy("customer_bucket")
        .option("checkpointLocation", f"{checkpoint_root}/feature_7d")
        .start(f"{feature_delta}/window_7d")
    )

    def _project_24h_for_mongo(df):
        rn = F.row_number().over(
            Window.partitionBy("customer_id").orderBy(F.col("window_end").desc())
        )
        return (
            df.withColumn("rn", rn)
            .where("rn = 1")
            .drop("rn", "customer_bucket", "discount_24h_myr")
            .withColumn("updated_at", F.current_timestamp())
            .withColumn(
                "quality",
                F.struct(
                    F.lit("spark_batch").alias("computed_via"),
                    F.lit(0.95).alias("confidence"),
                ),
            )
            .withColumn(
                "lineage",
                F.struct(F.col("window_end").alias("latest_source_at")),
            )
            .drop("window_end")
        )

    def _project_7d_for_mongo(df):
        rn = F.row_number().over(
            Window.partitionBy("customer_id").orderBy(F.col("window_end").desc())
        )
        return (
            df.withColumn("rn", rn)
            .where("rn = 1")
            .drop("rn", "customer_bucket")
            .withColumn("updated_at", F.current_timestamp())
            .withColumn(
                "quality",
                F.struct(
                    F.lit("spark_batch").alias("computed_via"),
                    F.lit(0.95).alias("confidence"),
                ),
            )
            .withColumn(
                "lineage",
                F.struct(F.col("window_end").alias("latest_source_at")),
            )
            .drop("window_end")
        )

    def _mongo_sink_writer(projector):
        def _write(batch_df, batch_id):
            # Avoid `batch_df.rdd.isEmpty()` — it materializes an RDD stage on executors and
            # breaks foreachBatch's Python callback path (Spark 3.5 + Py4J).
            if batch_df.isEmpty():
                return
            shaped = projector(batch_df)
            w = _mongo_connection_cert_opts(shaped.write.format("mongodb").mode("append"))
            (
                w.option("database", mongo_db)
                .option("collection", "features")
                .option("operationType", "update")
                .option("idFieldList", "customer_id")
                .option("upsertDocument", "false")
                .save()
            )

        return _write

    q_24h_mongo = (
        feature_24h.writeStream.foreachBatch(_mongo_sink_writer(_project_24h_for_mongo))
        .option("checkpointLocation", f"{checkpoint_root}/feature_24h_mongo")
        .outputMode("update")
        .start()
    )

    q_7d_mongo = (
        feature_7d.writeStream.foreachBatch(_mongo_sink_writer(_project_7d_for_mongo))
        .option("checkpointLocation", f"{checkpoint_root}/feature_7d_mongo")
        .outputMode("update")
        .start()
    )

    lag_stream = (
        txn_stream.withColumn("processing_time", F.current_timestamp())
        .withColumn(
            "lag_seconds",
            (F.unix_timestamp("processing_time") - F.unix_timestamp("timestamp")).cast(
                DoubleType()
            ),
        )
        .select("transaction_id", "customer_id", "timestamp", "processing_time", "lag_seconds")
    )

    q_lag = (
        lag_stream.writeStream.format("delta")
        .outputMode("append")
        .option("checkpointLocation", f"{checkpoint_root}/lag")
        .start(lag_delta)
    )

    queries = [
        ("feature_24h_delta", q_24h),
        ("feature_7d_delta", q_7d),
        ("feature_24h_mongo", q_24h_mongo),
        ("feature_7d_mongo", q_7d_mongo),
        ("lag_delta", q_lag),
    ]
    print(
        "Streaming queries active:",
        [(name, q.id) for name, q in queries],
        f"(pid={os.getpid()}, Ctrl+C or SIGTERM to stop)",
        flush=True,
    )

    # `awaitAnyTermination()` blocks Python in a Py4J java call, swallowing signals.
    # An Event polled on a python loop fires the moment the handler runs.
    stop_event = threading.Event()

    def _request_stop(signum, _frame):
        # os.write to fd 2 avoids any python io buffering problems.
        try:
            os.write(2, f"\nReceived signal {signum}; stopping streams...\n".encode())
        except Exception:
            pass
        stop_event.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    exit_code = 0
    try:
        while not stop_event.is_set():
            for name, q in queries:
                exc = q.exception()
                if exc is not None:
                    print(
                        f"Query {name} ({q.id}) terminated with exception: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    exit_code = 1
                    stop_event.set()
                    break
            stop_event.wait(timeout=2)
    finally:
        print("Stopping streams...", flush=True)
        for name, q in queries:
            try:
                q.stop()
            except Exception as e:
                print(f"  warn: q.stop() {name}: {e}", file=sys.stderr, flush=True)
        try:
            spark.stop()
        except Exception as e:
            print(f"  warn: spark.stop(): {e}", file=sys.stderr, flush=True)
        print("Streams stopped.", flush=True)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
