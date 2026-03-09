import crypto from "node:crypto";
import { gzipSync, gunzipSync } from "node:zlib";
import {
  GetObjectCommand,
  HeadObjectCommand,
  ListObjectsV2Command,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import { getPrefix, isS3Configured } from "./db";
import { getProjects } from "./projects";
import type {
  ServingCodeHistoryChunk,
  ServingLiveIndex,
  ServingLogPage,
  ServingManifest,
  ServingObjectRef,
} from "./serving-types";
import type { Project, Trajectory } from "@/lib/types";

type SourceObject = {
  key: string;
  eTag?: string;
  sizeBytes: number;
  lastModified?: string;
};

let servingS3Client: S3Client | undefined;
const SERVING_FORMAT_VERSION = "2026-03-08-v8";

function getServingS3Client(): S3Client {
  if (!servingS3Client) {
    servingS3Client = new S3Client({
      region: process.env.AWS_REGION ?? "us-east-1",
      credentials: {
        accessKeyId: process.env.AWS_ACCESS_KEY_ID ?? "",
        secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY ?? "",
      },
    });
  }
  return servingS3Client;
}

function getServingBucket(): string {
  return getPrefix();
}

function sanitizeEtag(value: string | undefined): string | undefined {
  if (!value) {
    return undefined;
  }
  return value.replaceAll('"', "");
}

/** Build the S3 key for a project's serving manifest. */
export function servingManifestKey(project: string): string {
  return `project/${project}/serving/manifest.json`;
}

/** Build the S3 key for a project's revisioned serving directory. */
export function servingRevisionPrefix(
  project: string,
  revision: string,
): string {
  return `project/${project}/serving/revisions/${revision}`;
}

/** Return UI-visible projects, excluding the legacy project. */
export async function getServingProjects(): Promise<Project[]> {
  const projects = await getProjects();
  return projects.filter((project) => project.name !== "legacy");
}

/** Read a JSON object from S3 and parse it. */
export async function readServingJson<T>(key: string): Promise<T | undefined> {
  if (!isS3Configured()) {
    return undefined;
  }

  try {
    const response = await getServingS3Client().send(
      new GetObjectCommand({
        Bucket: getServingBucket(),
        Key: key,
      }),
    );
    const body = await response.Body?.transformToString("utf-8");
    if (!body) {
      return undefined;
    }
    return JSON.parse(body) as T;
  } catch {
    return undefined;
  }
}

/** Read a gzipped NDJSON object from S3 and return parsed rows. */
export async function readServingGzipNdjson<T>(
  key: string,
): Promise<T[] | undefined> {
  if (!isS3Configured()) {
    return undefined;
  }

  try {
    const response = await getServingS3Client().send(
      new GetObjectCommand({
        Bucket: getServingBucket(),
        Key: key,
      }),
    );
    const bytes = await response.Body?.transformToByteArray();
    if (!bytes) {
      return undefined;
    }
    const text = gunzipSync(Buffer.from(bytes)).toString("utf-8");
    return text
      .split("\n")
      .filter((line) => line.trim().length > 0)
      .map((line) => JSON.parse(line) as T);
  } catch {
    return undefined;
  }
}

/** Head an S3 object and return the normalized metadata used by serving refs. */
export async function headServingObject(
  key: string,
  contentEncoding?: string,
): Promise<ServingObjectRef | undefined> {
  if (!isS3Configured()) {
    return undefined;
  }

  try {
    const response = await getServingS3Client().send(
      new HeadObjectCommand({
        Bucket: getServingBucket(),
        Key: key,
      }),
    );
    return {
      key,
      eTag: sanitizeEtag(response.ETag),
      sizeBytes: Number(response.ContentLength ?? 0),
      contentEncoding,
    };
  } catch {
    return undefined;
  }
}

/** Load the latest serving manifest for a project. */
export async function readServingManifest(
  project: string,
): Promise<ServingManifest | undefined> {
  return readServingJson<ServingManifest>(servingManifestKey(project));
}

/** Upload a JSON serving object and return its normalized reference. */
export async function putServingJson(
  key: string,
  value: unknown,
): Promise<ServingObjectRef> {
  const body = JSON.stringify(value);
  const response = await getServingS3Client().send(
    new PutObjectCommand({
      Bucket: getServingBucket(),
      Key: key,
      Body: body,
      ContentType: "application/json",
    }),
  );
  return {
    key,
    eTag: sanitizeEtag(response.ETag),
    sizeBytes: Buffer.byteLength(body),
  };
}

/** Upload a gzipped NDJSON serving object and return its normalized reference. */
export async function putServingGzipNdjson(
  key: string,
  rows: unknown[],
): Promise<ServingObjectRef> {
  const text = rows.map((row) => JSON.stringify(row)).join("\n");
  const body = gzipSync(Buffer.from(text, "utf-8"));
  const response = await getServingS3Client().send(
    new PutObjectCommand({
      Bucket: getServingBucket(),
      Key: key,
      Body: body,
      ContentType: "application/x-ndjson",
      ContentEncoding: "gzip",
    }),
  );
  return {
    key,
    eTag: sanitizeEtag(response.ETag),
    sizeBytes: body.byteLength,
    contentEncoding: "gzip",
  };
}

/** List the raw trajectory source objects that define the current serving revision. */
export async function listServingSourceObjects(
  project: string,
): Promise<SourceObject[]> {
  if (!isS3Configured()) {
    return [];
  }

  const prefix = `project/${project}/trajectories/`;
  const client = getServingS3Client();
  const objects: SourceObject[] = [];
  let continuationToken: string | undefined;

  do {
    const response = await client.send(
      new ListObjectsV2Command({
        Bucket: getServingBucket(),
        Prefix: prefix,
        ContinuationToken: continuationToken,
      }),
    );
    for (const item of response.Contents ?? []) {
      const key = item.Key;
      if (!key) {
        continue;
      }
      const isServingSource =
        key.endsWith("/trace.parquet") ||
        key.endsWith("/logs.parquet") ||
        key.endsWith("/code_snapshots.parquet");
      if (!isServingSource) {
        continue;
      }
      objects.push({
        key,
        eTag: sanitizeEtag(item.ETag),
        sizeBytes: Number(item.Size ?? 0),
        lastModified: item.LastModified?.toISOString(),
      });
    }
    continuationToken = response.IsTruncated
      ? response.NextContinuationToken
      : undefined;
  } while (continuationToken);

  return objects.sort((left, right) => left.key.localeCompare(right.key));
}

/** Compute a source revision hash from the raw trajectory objects in S3. */
export async function computeServingSourceRevision(
  project: string,
): Promise<string> {
  const objects = await listServingSourceObjects(project);
  const hash = crypto.createHash("sha256");
  hash.update(project);
  hash.update(SERVING_FORMAT_VERSION);
  for (const object of objects) {
    hash.update(object.key);
    hash.update(object.eTag ?? "");
    hash.update(`${object.sizeBytes}`);
    hash.update(object.lastModified ?? "");
  }
  return hash.digest("hex");
}

/** Read the trajectory list JSON for a serving manifest. */
export async function readServingTrajectories(
  manifest: ServingManifest,
): Promise<Trajectory[]> {
  const trajectories = await readServingJson<Trajectory[]>(
    manifest.objects.trajectoriesIndex.key,
  );
  return trajectories ?? [];
}

/** Read the compare index JSON for a serving manifest. */
export async function readServingCompare(
  manifest: ServingManifest,
): Promise<Trajectory[]> {
  const compare = await readServingJson<Trajectory[]>(
    manifest.objects.compareIndex.key,
  );
  return compare ?? [];
}

/** Read the setups index JSON for a serving manifest. */
export async function readServingSetups(
  manifest: ServingManifest,
): Promise<Trajectory[]> {
  const setups = await readServingJson<Trajectory[]>(
    manifest.objects.setupsIndex.key,
  );
  return setups ?? [];
}

/** Read the live index JSON for a serving manifest. */
export async function readServingLiveIndex(
  manifest: ServingManifest,
): Promise<ServingLiveIndex> {
  const live = await readServingJson<ServingLiveIndex>(
    manifest.objects.liveIndex.key,
  );
  return (
    live ?? {
      revision: manifest.revision,
      updatedAt: manifest.publishedAt,
      trajectoryIds: [],
      liveTrajectoryCount: 0,
    }
  );
}

/** Read a revisioned serving detail object for a trajectory. */
export async function readServingDetail(
  manifest: ServingManifest,
  trajectoryId: string,
): Promise<Trajectory | undefined> {
  const refs = manifest.objects.trajectories[trajectoryId];
  if (!refs) {
    return undefined;
  }
  const payload = await readServingJson<{
    revision: string;
    trajectory: Trajectory;
  }>(refs.detail.key);
  return payload?.trajectory;
}

/** Read a revisioned serving log page for a trajectory. */
export async function readServingLogs(
  manifest: ServingManifest,
  trajectoryId: string,
): Promise<ServingLogPage | undefined> {
  const refs = manifest.objects.trajectories[trajectoryId];
  if (!refs?.logs) {
    return undefined;
  }
  const rows = await readServingGzipNdjson<ServingLogPage["rows"][number]>(
    refs.logs.key,
  );
  if (!rows) {
    return undefined;
  }
  return {
    revision: manifest.revision,
    rows,
  };
}

/** Read the first revisioned serving code-history chunk for a trajectory. */
export async function readServingCodeHistory(
  manifest: ServingManifest,
  trajectoryId: string,
): Promise<ServingCodeHistoryChunk | undefined> {
  const refs = manifest.objects.trajectories[trajectoryId];
  const firstChunk = refs?.codeHistory?.[0];
  if (!firstChunk) {
    return undefined;
  }
  return readServingJson<ServingCodeHistoryChunk>(firstChunk.key);
}
