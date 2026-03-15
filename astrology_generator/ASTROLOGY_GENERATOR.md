# Astrology YAML Generator

Operator note:
- This is public code for a private-data workflow.
- By default it targets the detected private `astrology/` directory under `STATE_ZERO_PRIVATE_ROOT`.
- It is meant to generate `natal.yaml` and `dasha_periods.yaml` outside the repository tree.

This project now includes one public command that can generate the two private astrology lookup files used by State Zero:

- `natal.yaml`
- `dasha_periods.yaml`

Run it from the repo root:

```bash
python3 astrology_generator/generate_astrology_yaml.py
```

## What it asks for

- Birth date
- Birth time
- Timezone offset
- Latitude
- Longitude
- Dasha output start date
- Dasha output end date
- Output folder

For most India-based usage, you can just use:

- Timezone offset: `5.5`

If you do not know what "timezone offset" means, use `5.5` for India.

Latitude and longitude should be typed in decimal format like:

- Latitude: `19.0760`
- Longitude: `72.8777`

The user-facing flow does not ask for a place label anymore. The script keeps a simple internal fallback like `India` unless you explicitly pass `--birthplace`.

If you run it from inside this repo and your private folder is already set up, the default output folder will be your detected private `astrology/` directory.

## Overwrite safety

If `natal.yaml` or `dasha_periods.yaml` already exists in the output folder:

- interactive mode will ask before replacing them
- non-interactive mode will refuse to overwrite unless you add `--overwrite-existing`
- existing files are backed up into `backups/` before replacement

## Advanced CLI usage

You can also pass everything as flags:

```bash
python3 astrology_generator/generate_astrology_yaml.py \
  --non-interactive \
  --birth-date 2000-01-15 \
  --birth-time 09:30 \
  --timezone 5.5 \
  --latitude 19.0760 \
  --longitude 72.8777 \
  --dasha-start-date 2026-01-01 \
  --dasha-end-date 2036-07-01 \
  --output-dir "/tmp/example-astrology" \
  --overwrite-existing
```

Optional expert mode:

```bash
python3 astrology_generator/generate_astrology_yaml.py --validate
```

That mode samples live dates from the API and checks whether the generated Prana periods match the upstream response.

Defaults:

- `--timezone` defaults to `5.5`
- `--dasha-start-date` defaults to today
- `--dasha-end-date` defaults to 10 years after today
- `--output-dir` defaults to the detected private `astrology/` folder when running inside this repo, otherwise the current working directory
- internal birthplace label defaults to `India`

## What the tool does

### Natal generation

It calls the exposed VedicRishi endpoint with `apiName="kp_details"` and transforms the response into this project’s natal schema:

- `ascendant`
- `moon_nakshatra`
- planets with `sign`, `house`, and `dignity`

Important:
- the raw API house number is **not** used directly
- this project expects **whole-sign houses**, so the tool computes house numbers from the ascendant sign
- dignity is computed locally using explicit standard rules

### Dasha generation

It calls the exposed endpoint with `apiName="current_vdasha_date"` to fetch Pratyantar boundaries, then computes all Sookshma and Prana periods locally using Vimshottari ratios.

This avoids the old problem where multiple Pranas can happen inside one day and simple day-by-day polling misses them.

## Output contract

The tool preserves the same schema the pipeline already expects:

### `natal.yaml`

- `natal.ascendant`
- `natal.moon_nakshatra`
- `natal.planets.<Planet>.sign`
- `natal.planets.<Planet>.house`
- `natal.planets.<Planet>.dignity`

### `dasha_periods.yaml`

- `periods[]`
- each item includes:
  - `start`
  - `end`
  - `maha`
  - `antar`
  - `pratyantar`
  - `sookshma`
  - `prana`

## Provider note

The current implementation uses the exposed endpoint on:

- [https://vedicrishi.in/api/vedicrishi](https://vedicrishi.in/api/vedicrishi)

That endpoint is unofficial and may change or disappear. The code is intentionally split so the provider can later be replaced without changing the public CLI contract.

Reference docs for the official vendor API:

- [KP Details API](https://www.vedicrishiastro.com/docs/api/KP-Details-API)
- [Current Vimshottari Dasha API](https://www.vedicrishiastro.com/docs/api/Current-Vimshottari-Dasha-API)

## Quickest way to use it

### Interactive

```bash
cd "$HOME/State Zero"
python3 astrology_generator/generate_astrology_yaml.py
```

### One-line shell command

```bash
cd "$HOME/State Zero"
python3 astrology_generator/generate_astrology_yaml.py --non-interactive --birth-date 2000-01-15 --birth-time 09:30 --timezone 5.5 --latitude 19.0760 --longitude 72.8777 --dasha-start-date 2026-01-01 --dasha-end-date 2026-12-31 --output-dir "$HOME/State Zero Private/astrology" --overwrite-existing
```
