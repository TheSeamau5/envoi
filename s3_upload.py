from __future__ import annotations

import json
import os

import boto3
from botocore.exceptions import ClientError

from models import TurnRecord

_s3_client = None


def get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
    return _s3_client


def get_bucket() -> str:
    return os.environ.get("AWS_S3_BUCKET", "envoi-trace-data")


def append_jsonl_record(trajectory_id: str, record: TurnRecord) -> None:
    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/trajectory.jsonl"

    line = record.model_dump_json() + "\n"

    try:
        existing = s3.get_object(Bucket=bucket, Key=key)
        existing_data = existing["Body"].read()
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            existing_data = b""
        else:
            raise

    new_data = existing_data + line.encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=new_data)


def upload_file(trajectory_id: str, filename: str, data: bytes) -> str:
    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/{filename}"
    s3.put_object(Bucket=bucket, Key=key, Body=data)
    return f"s3://{bucket}/{key}"


def upload_text(trajectory_id: str, filename: str, content: str) -> str:
    return upload_file(trajectory_id, filename, content.encode("utf-8"))


def read_jsonl(trajectory_id: str) -> list[dict]:
    s3 = get_s3_client()
    bucket = get_bucket()
    key = f"trajectories/{trajectory_id}/trajectory.jsonl"

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        data = obj["Body"].read().decode("utf-8")
        return [json.loads(line) for line in data.strip().split("\n") if line]
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return []
        raise
