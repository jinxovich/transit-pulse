import type { Health, IngestStats, MetricsSummary, QualityMetrics } from "@contract";
import { getJson } from "./rest";

export const fetchMetricsSummary = () => getJson<MetricsSummary>("/api/v1/metrics/summary");
export const fetchQuality = () => getJson<QualityMetrics>("/api/v1/metrics/quality");
export const fetchIngestStats = () => getJson<IngestStats>("/api/v1/ingest/stats");
export const fetchHealth = () => getJson<Health>("/api/v1/health");
