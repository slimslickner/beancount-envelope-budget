# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `E010` reports a plan whose metadata has no effect: `target` without `by`,
  `by` without `target`, or `monthly` with `target`.
- The report view exposes each envelope's funding `level` and whether it is
  under, on or over it, for the CLI and Fava.

### Changed

- `fund --mode topup` funds a goal envelope (`target` + `by`) up to its on-track
  level for the run date, so a plan that raises a target mid-month now funds the
  difference, and reruns stay safe.
- A goal envelope's level grows linearly from the month its target first took
  effect to its `by` month, counting both ends.
- `suggest` and the report evaluate a plan at the latest date (or the month a
  cell represents), so a `plan` dated mid-month is reflected.
- The report and Fava view order parent headings and envelopes alphabetically,
  with ungrouped envelopes last, instead of declaration order. A leading number
  sorts as text, so a zero-padded prefix (`01`, `02`, `10`) controls the order.
- `fund --mode topup` prints `; OVERFUNDED <name>: ... above plan` for a goal
  envelope already above its level.

### Removed

- The `ahead` metadata key. A buffer is an explicit `allocate`; an envelope's
  level is already its planned balance.
