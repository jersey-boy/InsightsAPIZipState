# ZIP Reference File & Error Messages — Explained

This document explains two things:

1. How the `ZIP_Locale_Detail.xlsx` reference file is structured and how the
   validation script turns it into a ZIP → state lookup.
2. What each problem message in the output means now that the script uses this
   reference.

It reflects the behaviour of `pull_zip_state_federal.py` in **delivery mode**
(`ZIP_MODE = "delivery"`), which is the current default.

> Note: this is my (the developer's) working understanding of the USPS
> `ZIP_Locale_Detail` workbook based on inspecting its contents. It is not
> official USPS documentation. The column names and row layouts described here
> were read directly from the file in this repo.

---

## 1. The reference file: `ZIP_Locale_Detail.xlsx`

This is a USPS-style "locale detail" workbook. A *locale* is essentially a post
office / delivery unit. The workbook maps ZIP codes to the physical location
(state, city, facility ZIP) that serves them.

### 1.1 Three sheets

The workbook has three sheets. Together they cover the full ZIP universe; each
one is a different slice of it.

| Sheet | Rows | Real header row | What it holds |
|-------|-----:|:---------------:|---------------|
| `Detail` | 42,288 | row 1 | The main locale list — ordinary delivery ZIPs and the facilities that serve them. |
| `Unique` | 2,234 | row 3 | "Unique" ZIPs assigned to a single organization (universities, big companies, government offices). |
| `Other`  | 2,400 | row 3 | Remaining ZIPs that aren't in `Detail` (e.g. PO-box-only ZIPs and other special cases). |

**Why three sheets matter:** the `Detail` sheet alone does *not* contain every
ZIP. Roughly 195 valid member ZIPs (things like `00544` Holtsville NY, `08544`
Princeton NJ, `02912` Brown University RI) appear only on `Unique` or `Other`.
If we used `Detail` alone, all of those would be wrongly reported as unknown
ZIPs. The script therefore **merges all three sheets** into one lookup.

### 1.2 The header quirk

The sheets are not laid out identically:

- **`Detail`** has a clean, single-row header. Columns are named exactly, e.g.
  `DELIVERY ZIPCODE`, `PHYSICAL STATE`, `PHYSICAL ZIP`, `PHYSICAL CITY`,
  `ZIP CLASS CODE`.
- **`Unique`** and **`Other`** have a **three-row stacked header**. The real
  column names sit on the third row (header index `2` when read with pandas).
  Because the labels "PHYSICAL STATE" and "PHYSICAL ZIP" are split across
  stacked cells, pandas collapses them to just `STATE` and `ZIP`, and the
  delivery ZIP column is named `ZIPCODE` (not `DELIVERY ZIPCODE`).

The script accounts for this by reading each sheet with the correct header row
and mapping each sheet's own column names to a common meaning:

| Meaning | `Detail` column | `Unique` / `Other` column |
|---------|-----------------|---------------------------|
| Delivery ZIP | `DELIVERY ZIPCODE` | `ZIPCODE` |
| Physical (facility) ZIP | `PHYSICAL ZIP` | `ZIP` |
| State | `PHYSICAL STATE` | `STATE` |

### 1.3 Delivery ZIP vs. physical ZIP

Each locale row carries two ZIPs:

- **Delivery ZIP** — the ZIP a member would actually write on their mail. This
  is what members type into NationBuilder, so it is the natural thing to
  validate against. Broadest coverage (~41,486 combined unique delivery ZIPs).
- **Physical ZIP** — the ZIP of the *facility* (post office) that serves the
  delivery ZIP. Narrower (~29,331 unique). Many delivery ZIPs roll up to a
  smaller number of physical facility ZIPs.

Both modes validate against the same `PHYSICAL STATE` value — the workbook only
carries one state per row. The mode only changes **which ZIP column is used as
the lookup key**:

- `ZIP_MODE = "delivery"` → key on the delivery ZIP. **This is the default and
  the recommended setting**, because it matches how members enter ZIPs and it
  gives the widest coverage.
- `ZIP_MODE = "physical"` → key on the physical facility ZIP. Coverage is much
  narrower, so ~3,100 unique member ZIPs would fail to match and be reported as
  "not found." Only use this if you specifically want to check the physical
  facility ZIP.

### 1.4 How the lookup is built

The script builds a single Python dictionary `{ zip:int -> state:str }`:

1. Read `Detail`, then `Unique`, then `Other` (in that order).
2. For each row, take the ZIP column for the chosen mode and the state column,
   coerce the ZIP to an integer, upper-case and trim the state.
3. Insert into the dictionary with **"first sheet wins"** semantics (`Detail`
   is authoritative; `Unique`/`Other` only fill in ZIPs `Detail` doesn't
   already have).

**Why ZIPs are stored as integers.** The Insights view stores the member ZIP
as a number (`registered_zip_clean`, e.g. `1002`), and the reference file also
stores ZIPs as numbers (`1002`, not `01002`). Converting both sides to integers
makes the leading zero a non-issue — `01002` and `1002` compare equal. The ZIP
is only re-formatted back to a zero-padded 5-digit string (`01002`) for display
in the output files, *after* the comparison has run.

The combined lookup currently holds **~41,485 ZIP keys** in delivery mode.

---

## 2. What the error messages mean

The script writes two problem columns onto every record:

- `zip_state_problem`
- `federal_district_problem`

A **blank** value means that check **passed**. A non-blank value is a specific
problem message. Below is what each message means and how seriously to treat it.

### 2.1 `zip_state_problem` — ZIP vs. state check

This check answers: *does the member's ZIP actually belong to the state they
listed?* It looks the member's ZIP up in the reference and compares the state
the reference reports against the member's `registered_state`.

| Message | Meaning | How to read it |
|---------|---------|----------------|
| *(blank)* | The ZIP was found and its state matches the member's state. | OK — no action. |
| `ZIP STATE PROBLEM. ZIP IS BLANK` | The member has no ZIP on file (missing/empty). | Data gap — the member needs a ZIP. Not a mismatch, just absent. |
| `ZIP STATE PROBLEM. ZIP NOT FOUND` | The ZIP is present and well-formed, but it does **not** exist in the reference (not on any of the three sheets). | See note below — mostly placeholder / unassigned ZIPs, not a state mismatch. |
| `ZIP STATE PROBLEM. SHOULD BE XX` | The ZIP was found, but the reference says it belongs to state `XX`, which differs from the member's listed state. | **The actionable one.** The member's ZIP and state disagree; `XX` is the state the ZIP maps to. |

**About `ZIP NOT FOUND` specifically.** After merging all three sheets, the
ZIPs that still come back "not found" are *not* non-US or military/APO ZIPs — we
checked, and none fall in the military (090xx–098xx) or territory ranges. They
are overwhelmingly **placeholder or unassigned ZIPs** — values like `20000`,
`30000`, `37000` that look like a ZIP but aren't real delivery points. So
`ZIP NOT FOUND` now means: *"this is a syntactically valid 5-digit number that
does not correspond to any real USPS ZIP,"* which usually points to a typo or a
made-up value rather than a genuine state mismatch. It is deliberately **not**
labelled "non-US/APO" because that would be inaccurate.

**Precedence.** The checks are evaluated in this order: blank ZIP → not found →
match/mismatch. So a member is only ever given one `zip_state_problem` message,
the first that applies.

### 2.2 `federal_district_problem` — federal district vs. state check

This check is independent of the ZIP reference file. It uses `fed_dist_prefix`,
the two-letter state code embedded in the federal district value (e.g. the
district `AK0` has prefix `AK`). It answers: *does the federal district's state
prefix match the member's listed state?*

| Message | Meaning | How to read it |
|---------|---------|----------------|
| *(blank)* | The federal district prefix matches the member's state. | OK — no action. |
| `FEDERAL DISTRICT PROBLEM. FEDERAL DISTRICT IS BLANK` | The member has no federal district assigned. | Data gap — the district hasn't been populated for this member. |
| `FEDERAL DISTRICT PROBLEM` | The federal district is present, but its state prefix does not match the member's state. | Mismatch — the district and the state disagree. |

> `fed_dist_prefix` is only used to run this check; it is dropped from the final
> output files, so you won't see it as a column.

### 2.3 Which records land in each problems report

The two checks are reported **separately**, one file per test:

- `zip_state_problems.csv` / `.xlsx` — every record whose `zip_state_problem` is
  non-blank. Carries the ZIP/state flag only.
- `federal_district_problems.csv` / `.xlsx` — every record whose
  `federal_district_problem` is non-blank. Carries the federal-district flag
  only.

The reports are independent, so a record that fails **both** checks appears in
**both** files (each showing that file's own flag). The full dataset file
(`zip_state_federal_district.csv` / `.xlsx`) still carries both flag columns for
every record if you want to see the two results side by side.

---

## 3. Current run at a glance (delivery mode)

From the most recent run over **177,923** member records:

**ZIP / state check**

| Result | Count |
|--------|------:|
| No problem (blank) | 177,833 |
| `ZIP NOT FOUND` (placeholder / unassigned ZIPs) | 50 |
| `SHOULD BE XX` (real state mismatches) | 40 |
| **Total ZIP problems** | **90** |

**Federal district check**

| Result | Count |
|--------|------:|
| No problem (blank) | 176,947 |
| `FEDERAL DISTRICT IS BLANK` | 935 |
| `FEDERAL DISTRICT PROBLEM` (mismatch) | 41 |
| **Total federal district problems** | **976** |

**Rows written per report:** `zip_state_problems` = 90, `federal_district_problems`
= 976. (A record failing both checks appears in both files, so these are not
mutually exclusive.)

For comparison, using only the `Detail` sheet produced 451 `ZIP NOT FOUND`
rows; merging the `Unique` and `Other` sheets recovered 195 of those valid ZIPs,
which is why the not-found count is now much lower and mostly genuine junk.

---

## 4. Switching modes

To validate against the physical facility ZIP instead of the delivery ZIP, set
this one constant near the top of `pull_zip_state_federal.py`:

```python
ZIP_MODE = "physical"   # default is "delivery"
```

Everything else — the three-sheet merge, the integer matching, the error
messages — stays the same. Expect many more `ZIP NOT FOUND` results in physical
mode because the physical ZIP column covers fewer ZIPs.
