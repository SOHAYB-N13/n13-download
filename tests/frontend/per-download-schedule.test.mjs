// Focused tests for the New-download dialog's schedule validation.
//
// Loads the real add-download module and exercises the pure helpers the dialog
// uses, so `00:00` can never become a midnight schedule and equal boundaries
// cannot produce a window that never opens.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(
  __dirname,
  "../../ui/frontend/js/features/add-download/add-download.js",
);

const AddDownload = (0, eval)(fs.readFileSync(SRC, "utf8") + "\n;AddDownload");

test("00:00 is the unset sentinel, never a midnight schedule", () => {
  assert.equal(AddDownload.normalizeScheduleTime("00:00"), "");
  assert.equal(AddDownload.normalizeScheduleTime("0:00"), "");
  const both = AddDownload.validateSchedule("00:00", "00:00");
  assert.deepEqual(both, { ok: true, error: "", start: "", end: "" });
});

test("an empty or cleared field means not configured", () => {
  for (const value of ["", "   ", null, undefined]) {
    assert.equal(AddDownload.normalizeScheduleTime(value), "", String(value));
  }
});

test("times are canonicalised to HH:MM", () => {
  assert.equal(AddDownload.normalizeScheduleTime("9:5"), "09:05");
  assert.equal(AddDownload.normalizeScheduleTime("23:59"), "23:59");
});

test("malformed times are rejected", () => {
  for (const bad of ["24:00", "12:60", "noon", "12", "12:34:56", "-1:00"]) {
    assert.equal(AddDownload.normalizeScheduleTime(bad), null, bad);
    assert.equal(AddDownload.validateSchedule(bad, "").ok, false, bad);
  }
});

test("either boundary may be set independently", () => {
  assert.deepEqual(AddDownload.validateSchedule("09:00", ""), {
    ok: true, error: "", start: "09:00", end: "",
  });
  assert.deepEqual(AddDownload.validateSchedule("", "17:00"), {
    ok: true, error: "", start: "", end: "17:00",
  });
});

test("equal non-zero boundaries are rejected", () => {
  const result = AddDownload.validateSchedule("09:00", "09:00");
  assert.equal(result.ok, false);
  assert.equal(result.error, "same");
});

test("an overnight window (start after end) is valid", () => {
  const result = AddDownload.validateSchedule("23:00", "07:00");
  assert.equal(result.ok, true);
  assert.equal(result.start, "23:00");
  assert.equal(result.end, "07:00");
});
