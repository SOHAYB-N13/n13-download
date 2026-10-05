// Download groups — pure frontend logic.
//
// `groups-model.js` is deliberately free of DOM, API and module state, which is
// what makes it testable here.  The rules worth guarding hardest:
//
//   * progress is `null` — never `0` — when the total size is unknown, because a
//     confident "0%" bar is a number nobody has;
//   * `validateDraft` mirrors the backend's name rules, so the two layers cannot
//     disagree about what a valid name is;
//   * a task with no `project_id` belongs to Default, which is what the backend
//     does — if the two disagreed, a task would be invisible in every tab.
//
// Three cross-layer guards follow: the frontend's state vocabulary must match
// `projects/models.py`, its delete modes must match `DeleteMode`, and every
// state / reason / error the backend can produce must have a translation.  That
// last one is the class of bug that shows a raw `snake_case` code to the user.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import assert from "node:assert/strict";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../..");

const read = (rel) => fs.readFileSync(path.join(ROOT, rel), "utf8");

// Evaluate just the `const GroupsModel = {...}` literal.
const src = read("ui/frontend/js/features/groups/groups-model.js");
const start = src.indexOf("const GroupsModel = {");
assert.ok(start !== -1, "could not find `const GroupsModel = {`");
const body = src.slice(start);
const literal = body.slice(body.indexOf("{"), body.lastIndexOf("};") + 1);
const GroupsModel = eval("(" + literal + ")");

// Evaluate the i18n dictionaries so the coverage guards can check them.
const i18nSrc = read("ui/frontend/js/i18n.js");
const i18nBody = i18nSrc.slice(i18nSrc.indexOf("const I18N = {"));
const I18N = eval("(" + i18nBody.slice(i18nBody.indexOf("{"), i18nBody.lastIndexOf("};") + 1) + ")");
const EN = new Set(Object.keys(I18N.dict.en));
const FA = new Set(Object.keys(I18N.dict.fa));

const MODELS_PY = read("projects/models.py");
const VIEWS_PY = read("projects/views.py");

/** The string values of a Python enum class, in source order. */
const enumValues = (cls, nextCls) => {
  const block = MODELS_PY.slice(MODELS_PY.indexOf("class " + cls), MODELS_PY.indexOf("class " + nextCls));
  return [...block.matchAll(/^\s{4}[A-Z_]+\s*=\s*"([a-z_]+)"/gm)].map((m) => m[1]);
};

/**
 * A stand-in for `I18N.fmt(key, vars, fallback)`.
 *
 * The real one looks the key up and substitutes into the *value*; these tests
 * have no dictionary, so the stub returns the key plus its variables.  Assertions
 * can therefore check both that the right key was chosen and that the right
 * values were passed — which is the part that actually breaks.
 */
const t = (key, vars) => (vars === undefined ? key : key + "|" + JSON.stringify(vars));

const group = (over = {}) => ({
  id: "p1",
  name: "Movies",
  description: "",
  directory: "",
  state: "queued",
  state_reason: "waiting_for_slot",
  status: "active",
  updated_at: 1,
  schedule: { enabled: false, start: "", stop: "", days: [] },
  schedule_open: true,
  counts: { total: 0, active: 0, queued: 0, paused: 0, failed: 0, downloaded_bytes: 0, total_bytes: 0 },
  ...over,
});

// ── States ────────────────────────────────────────────────────────────────

test("every state maps to an i18n key", () => {
  assert.ok(GroupsModel.STATES.length >= 9);
  for (const state of GroupsModel.STATES) {
    assert.equal(GroupsModel.stateKey(state), "groups.state." + state);
  }
});

test("an unrecognised state falls back to the empty key instead of leaking a raw value", () => {
  assert.equal(GroupsModel.stateKey("something_new"), "groups.state.empty");
});

test("every state has a colour tone", () => {
  for (const state of GroupsModel.STATES) {
    const tone = GroupsModel.stateTone(state);
    assert.ok(["ok", "bad", "warn", "info", "muted"].includes(tone), `${state} -> ${tone}`);
  }
});

test("the tones describe the state rather than decorating it", () => {
  assert.equal(GroupsModel.stateTone("running"), "ok");
  assert.equal(GroupsModel.stateTone("failed"), "bad");
  assert.equal(GroupsModel.stateTone("paused_by_user"), "warn");
  assert.equal(GroupsModel.stateTone("waiting_for_schedule"), "info");
});

test("the frontend state vocabulary matches the backend enum", () => {
  const backend = enumValues("ProjectState", "CompletionAction");
  assert.ok(backend.length >= 9, `expected a real enum, parsed ${backend.length}`);
  assert.deepEqual([...GroupsModel.STATES].sort(), [...backend].sort());
});

// ── Tab strip ─────────────────────────────────────────────────────────────

test("the strip is the All tab followed by the groups, in the order given", () => {
  const projects = [group({ id: "b", name: "B" }), group({ id: "a", name: "A" })];
  const tabs = GroupsModel.tabs(projects, "b");
  assert.deepEqual(tabs.map((x) => x.id), [GroupsModel.ALL_ID, "b", "a"]);
  assert.deepEqual(tabs.map((x) => x.active), [false, true, false]);
});

test("All is active when nothing else is selected", () => {
  const tabs = GroupsModel.tabs([group()], null);
  assert.equal(tabs[0].active, true);
  assert.equal(tabs[1].active, false);
  assert.equal(GroupsModel.isAll(null), true);
  assert.equal(GroupsModel.isAll(""), true);
  assert.equal(GroupsModel.isAll(GroupsModel.ALL_ID), true);
  assert.equal(GroupsModel.isAll("p1"), false);
});

test("a paused group is flagged on its tab", () => {
  const tabs = GroupsModel.tabs([group({ id: "p1", status: "paused" })], "p1");
  assert.equal(tabs[1].paused, true);
});

test("the All tab is never a real group", () => {
  // A synthetic id that collided with a real one would make "All" unreachable.
  const projects = [group({ id: GroupsModel.ALL_ID })];
  assert.equal(GroupsModel.tabs(projects, GroupsModel.ALL_ID)[0].id, GroupsModel.ALL_ID);
  assert.equal(GroupsModel.find(projects, GroupsModel.ALL_ID), null);
});

test("find returns the group or null", () => {
  const projects = [group({ id: "p1", name: "Movies" })];
  assert.equal(GroupsModel.find(projects, "p1").name, "Movies");
  assert.equal(GroupsModel.find(projects, "nope"), null);
  assert.equal(GroupsModel.find(projects, GroupsModel.ALL_ID), null);
});

// ── Display name ──────────────────────────────────────────────────────────

test("the Default group's name is a label, not user data", () => {
  // The backend seeds this one with the literal name "Default", so it must be
  // translated like any other label — otherwise it is the single untranslated
  // word in an otherwise fully localised tab strip.
  assert.equal(GroupsModel.displayName("Default", true, t), "groups.default_name");
  assert.ok(EN.has("groups.default_name"), "en is missing groups.default_name");
  assert.ok(FA.has("groups.default_name"), "fa is missing groups.default_name");
});

test("a user's own group name is shown verbatim", () => {
  // Even when the user literally types "Default", it is their data and is
  // passed through untouched.
  assert.equal(GroupsModel.displayName("Default", false, t), "Default");
  assert.equal(GroupsModel.displayName("Breaking Bad", false, t), "Breaking Bad");
});

test("a nameless group falls back to the untitled label", () => {
  assert.equal(GroupsModel.displayName("", false, t), "groups.untitled");
  assert.equal(GroupsModel.displayName(undefined, false, t), "groups.untitled");
  assert.equal(GroupsModel.displayName(null, false, t), "groups.untitled");
});

// ── Task scoping ──────────────────────────────────────────────────────────

test("All keeps every task", () => {
  const tasks = [{ id: "1", project_id: "a" }, { id: "2", project_id: "b" }];
  assert.equal(GroupsModel.tasksFor(tasks, GroupsModel.ALL_ID).length, 2);
  assert.equal(GroupsModel.tasksFor(tasks, null).length, 2);
});

test("a group keeps only its own tasks", () => {
  const tasks = [{ id: "1", project_id: "a" }, { id: "2", project_id: "b" }, { id: "3", project_id: "a" }];
  assert.deepEqual(GroupsModel.tasksFor(tasks, "a").map((x) => x.id), ["1", "3"]);
});

test("a task with no project_id belongs to Default, exactly as the backend has it", () => {
  const tasks = [{ id: "1" }, { id: "2", project_id: "" }];
  assert.deepEqual(GroupsModel.tasksFor(tasks, "default").map((x) => x.id), ["1", "2"]);
});

test("tasksFor never mutates the caller's list", () => {
  const tasks = [{ id: "1", project_id: "a" }];
  GroupsModel.tasksFor(tasks, GroupsModel.ALL_ID).push({ id: "2" });
  assert.equal(tasks.length, 1);
});

// ── Counts ────────────────────────────────────────────────────────────────

test("counts buckets every state the strip and header care about", () => {
  const tasks = [
    { id: "1", state: "Downloading" },
    { id: "2", state: "Queued" },
    { id: "3", state: "Paused" },
    { id: "4", state: "Failed" },
    { id: "5", state: "Complete" },
    { id: "6", state: "Cancelled" },
  ];
  const c = GroupsModel.counts(tasks);
  assert.equal(c.total, 6);
  assert.equal(c.active, 1);
  assert.equal(c.queued, 1);
  assert.equal(c.paused, 1);
  assert.equal(c.failed, 2);   // Failed + Cancelled
  assert.equal(c.completed, 1);
});

test("counts sum the byte totals and survive a missing size", () => {
  const c = GroupsModel.counts([
    { id: "1", completed: 100, total: 400 },
    { id: "2" },                       // no size known yet
    { id: "3", completed: null, total: null },
  ]);
  assert.equal(c.downloaded_bytes, 100);
  assert.equal(c.total_bytes, 400);
});

test("counts of nothing is all zeroes, not undefined", () => {
  const c = GroupsModel.counts([]);
  assert.equal(c.total, 0);
  assert.equal(c.active, 0);
  assert.equal(c.total_bytes, 0);
  assert.equal(GroupsModel.counts(null).total, 0);
});

// ── Progress ──────────────────────────────────────────────────────────────

test("progress is null when the total size is unknown", () => {
  // Null, never 0: a fabricated "0%" is a number nobody has.
  assert.equal(GroupsModel.progress({ total_bytes: 0, downloaded_bytes: 0 }), null);
  assert.equal(GroupsModel.progress({}), null);
  assert.equal(GroupsModel.progress(null), null);
});

test("progress is a percentage once the total is known", () => {
  assert.equal(GroupsModel.progress({ total_bytes: 1000, downloaded_bytes: 250 }), 25);
});

test("progress is clamped to 0..100", () => {
  assert.equal(GroupsModel.progress({ total_bytes: 100, downloaded_bytes: 500 }), 100);
  assert.equal(GroupsModel.progress({ total_bytes: 100, downloaded_bytes: -50 }), 0);
});

// ── Window / schedule helpers ─────────────────────────────────────────────

test("windowOpen defaults to open when the backend is silent", () => {
  assert.equal(GroupsModel.windowOpen({}), true);
  assert.equal(GroupsModel.windowOpen(null), true);
  assert.equal(GroupsModel.windowOpen({ schedule_open: false }), false);
});

test("hasSchedule only reports a configured, enabled window", () => {
  assert.equal(GroupsModel.hasSchedule(group({ schedule: { enabled: true } })), true);
  assert.equal(GroupsModel.hasSchedule(group({ schedule: { enabled: false } })), false);
  assert.equal(GroupsModel.hasSchedule(group({ schedule: null })), false);
});

test("a schedule summary names the days, or says every day", () => {
  const everyDay = GroupsModel.scheduleSummary({ enabled: true, start: "09:00", stop: "17:00", days: [] }, t);
  assert.match(everyDay, /every_day/);
  assert.match(everyDay, /09:00/);

  const someDays = GroupsModel.scheduleSummary({ enabled: true, start: "09:00", stop: "17:00", days: [0, 6] }, t);
  assert.match(someDays, /days/);
  assert.match(someDays, /groups\.day\.0/);
  assert.match(someDays, /groups\.day\.6/);
});

test("a disabled schedule has no summary", () => {
  assert.equal(GroupsModel.scheduleSummary({ enabled: false }, t), "");
  assert.equal(GroupsModel.scheduleSummary(null, t), "");
});

test("the counts summary only mentions buckets that are non-empty", () => {
  const quiet = GroupsModel.countsSummary({ total: 4 }, t);
  assert.match(quiet, /total/);
  assert.doesNotMatch(quiet, /active/);

  const busy = GroupsModel.countsSummary({ total: 4, active: 2, failed: 1 }, t);
  assert.match(busy, /active/);
  assert.match(busy, /failed/);
  assert.match(busy, / · /);
});

// ── Validation (must mirror the backend) ──────────────────────────────────

test("a name is required", () => {
  const res = GroupsModel.validateDraft({ name: "   " }, [], "");
  assert.equal(res.ok, false);
  assert.equal(res.field, "name");
  assert.equal(res.error, "name_required");
});

test("a name may not exceed the backend's limit", () => {
  const res = GroupsModel.validateDraft({ name: "x".repeat(GroupsModel.MAX_NAME_LENGTH + 1) }, [], "");
  assert.equal(res.error, "name_too_long");
});

test("a duplicate name is rejected case-insensitively", () => {
  const projects = [{ id: "p1", name: "Movies" }];
  assert.equal(GroupsModel.validateDraft({ name: "movies" }, projects, "").error, "name_duplicate");
  assert.equal(GroupsModel.validateDraft({ name: "My  Movies" }, [{ id: "p2", name: "My Movies" }], "").error, "name_duplicate");
});

test("editing a group does not clash with itself", () => {
  assert.equal(GroupsModel.validateDraft({ name: "Movies" }, [{ id: "p1", name: "Movies" }], "p1").ok, true);
});

test("a valid draft passes", () => {
  assert.equal(GroupsModel.validateDraft({ name: "  Movies  " }, [], "").ok, true);
});

test("concurrency outside the allowed range is rejected", () => {
  assert.equal(GroupsModel.validateDraft({ name: "A", max_concurrent: -1 }, [], "").error, "max_concurrent_range");
  assert.equal(
    GroupsModel.validateDraft({ name: "A", max_concurrent: GroupsModel.MAX_CONCURRENT_CEILING + 1 }, [], "").error,
    "max_concurrent_range"
  );
  assert.equal(GroupsModel.validateDraft({ name: "A", max_concurrent: "not a number" }, [], "").error, "max_concurrent_range");
});

test("an empty concurrency is the inherit value and is allowed", () => {
  assert.equal(GroupsModel.validateDraft({ name: "A", max_concurrent: "" }, [], "").ok, true);
  assert.equal(GroupsModel.validateDraft({ name: "A", max_concurrent: 0 }, [], "").ok, true);
});

test("an enabled schedule needs two valid times", () => {
  const bad = GroupsModel.validateDraft(
    { name: "A", schedule: { enabled: true, start: "nope", stop: "07:00" } }, [], ""
  );
  assert.equal(bad.error, "schedule_time_invalid");
  assert.equal(bad.field, "schedule_start");

  const badStop = GroupsModel.validateDraft(
    { name: "A", schedule: { enabled: true, start: "23:00", stop: "25:00" } }, [], ""
  );
  assert.equal(badStop.field, "schedule_stop");
});

test("a disabled schedule ignores its times", () => {
  const res = GroupsModel.validateDraft(
    { name: "A", schedule: { enabled: false, start: "", stop: "" } }, [], ""
  );
  assert.equal(res.ok, true);
});

test("the max name length matches the backend", () => {
  const match = MODELS_PY.match(/MAX_NAME_LENGTH\s*=\s*(\d+)/);
  assert.ok(match, "MAX_NAME_LENGTH not found in projects/models.py");
  assert.equal(GroupsModel.MAX_NAME_LENGTH, Number(match[1]));
});

// ── Delete modes ──────────────────────────────────────────────────────────

test("only one delete mode destroys files", () => {
  const destructive = GroupsModel.DELETE_MODES.filter((m) => GroupsModel.destroysFiles(m));
  assert.deepEqual(destructive, ["delete_files"]);
});

test("the delete modes match the backend enum", () => {
  const backend = enumValues("DeleteMode", "ProjectError");
  assert.deepEqual([...GroupsModel.DELETE_MODES].sort(), [...backend].sort());
});

// ── Cross-layer: every backend code has a translation ─────────────────────
//
// These are the keys the UI builds at runtime (`"groups.error." + code`), so a
// missing one is invisible to a grep and shows up as a raw snake_case code in a
// toast.  `invalid_delete_mode`, `invalid_status` and
// `invalid_completion_action` are deliberately excluded: they can only be
// produced by a caller passing a bad enum, which is a bug, and a raw code is
// the honest signal for one.

test("every project state has a label in both locales", () => {
  for (const state of GroupsModel.STATES) {
    const key = "groups.state." + state;
    assert.ok(EN.has(key), `missing en: ${key}`);
    assert.ok(FA.has(key), `missing fa: ${key}`);
  }
});

test("every reason the backend can send has a label in both locales", () => {
  // The tuple mixes literals with named constants that live in schedule.py, so
  // the identifiers have to be resolved rather than assumed to be literals.
  const consts = {};
  for (const m of read("projects/schedule.py").matchAll(/^(REASON_[A-Z_]+)\s*=\s*"([a-z_]+)"/gm)) {
    consts[m[1]] = m[2];
  }
  const block = VIEWS_PY.slice(VIEWS_PY.indexOf("STATE_REASONS"), VIEWS_PY.indexOf("__all__"));
  const reasons = [...block.matchAll(/^\s{4}(?:"([a-z_]+)"|([A-Z_][A-Z_0-9]*)),/gm)]
    .map((m) => m[1] || consts[m[2]])
    .filter(Boolean);
  assert.ok(reasons.length >= 13, `expected a real reason vocabulary, parsed ${reasons.length}`);
  for (const reason of reasons) {
    const key = "groups.reason." + reason;
    assert.ok(EN.has(key), `missing en: ${key}`);
    assert.ok(FA.has(key), `missing fa: ${key}`);
  }
});

test("every user-facing directory error has a label in both locales", () => {
  const validation = read("projects/validation.py");
  const codes = [...validation.matchAll(/"([a-z_][a-z_0-9]*)",\s*f?"/g)].map((m) => m[1]);
  assert.ok(codes.length >= 5, `expected real error codes, parsed ${codes.length}`);
  for (const code of codes) {
    const key = "groups.error." + code;
    assert.ok(EN.has(key), `missing en: ${key}`);
    assert.ok(FA.has(key), `missing fa: ${key}`);
  }
});

test("every day index has a label in both locales", () => {
  for (let d = 0; d < 7; d++) {
    assert.ok(EN.has("groups.day." + d), `missing en: groups.day.${d}`);
    assert.ok(FA.has("groups.day." + d), `missing fa: groups.day.${d}`);
  }
});
