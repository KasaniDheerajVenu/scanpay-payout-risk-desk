# ADR-004: Isolation Forest as a required anomaly detection component

Status: accepted design; actual model quality pending local validation.

## Context
The brief requires learning normal behaviour and known fraud patterns, but does not mandate an ML algorithm. The project uses a short history across 24 teams; multivariate anomalies may add value beyond single-feature statistics.

## Decision
Implement Isolation Forest training, chronological validation, inference and metadata reporting as required functionality. Attempt ML in normal evaluation, with explicit fallback when insufficient or unreliable. Use a pooled model with comparable relative features and earlier-only baselines. A validated actionable anomaly can trigger Manual review; diagnostic scores alone cannot. Never reject from ML alone. Retain median/IQR statistics and evidence-backed known fraud clustering. Persist model metadata and findings in existing decision JSONB, not an extra table.

## Alternatives
Statistics only, Local Outlier Factor, supervised classification. Sparse verified labels and the requirement for established known-fraud evidence make a supervised classifier unnecessary here.

## Consequences
Model pipeline is testable and integrated, while decision safety remains governed by policy. Model validation results must be measured and disclosed, not presumed. Fitted private artifacts remain local.
