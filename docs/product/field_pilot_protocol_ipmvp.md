# Proposed field pilot and measurement plan

This is an engineering experiment proposal, not IPMVP or ASHRAE certification. Earlier seven-day baseline, P95 guarantees, and preset saving/payback claims are withdrawn. A qualified measurement practitioner should determine the formal verification method and observation periods for the site.

## Establish measurement and history

Agree on a whole-facility meter or identified background channel. Verify clocks, units, calibration and counter resets against a reference meter. CT-only power uses nominal voltage/power factor and cannot independently certify energy, power-factor correction or utility demand charges.

Collect at least four weeks of usable hourly history as an initial software experiment, extending it for gaps, holidays, seasons, weather or production changes. Record opening hours, production, operator changes, and the effective supply/demand contract. Missing hours stay missing; valid zeros remain. Runtime ML selection needs 21 complete eligible feature days plus lag history, so four calendar weeks with gaps may be insufficient.

## Shadow operation

Issue all 24-hour forecasts before their origin with only then-available inputs. Track MAE, WAPE where aggregate consumption is positive, bias, baseline comparisons, missing days and origins. Track actual breaches against the agreed site capacity separately from envelope coverage. Default margins are policy buffers, not calibrated probabilities.

Review advice with staff. Verify windows, duration, critical loads and any double-counting of flexible loads in the background. Reject schedules with capacity slack or unmet operational requirements. Match measurement resolution to the contract's demand interval. No autonomous mains actuation is included.

## Controlled advisory intervention

After measurement/shadow results are accepted, staff can follow selected schedules. Log actions, completion, overrides, quality issues, production impact and forecast version. Compare matched periods or randomized eligible days where practical, with enough observations to distinguish effects from ordinary variation. Use an agreed fallback for stale data or deteriorating forecasts.

## Report measured outcomes

Compare observed energy and demand with an independently justified, adjusted counterfactual. Explain weather, production and opening-hour adjustments. Include uncertainty and negative outcomes.

Use the actual contract and billing intervals to value changes. Repeated projected alert penalties are not invoiced charges. Include hardware, installation, maintenance, connectivity, subscription and staff costs before estimating net ROI. Measurement alone does not correct physical power factor.

Pre-agree site-specific go/no-go criteria for completeness, forecast improvement, operational acceptance, measurable net benefit and support effort. Targets are not achieved results or guarantees.

## Deployment controls

Configure `API_KEY`, TLS, backups, restricted access and secrets; populate firmware `EMS_API_KEY`. HTTP 409 means a stale/duplicate reading was not billed. Historical faulty aggregates need a separate audit/rebuild before financial use.

Multi-customer service requires tenant authorization and device credential lifecycle management. Physical installation, certification and meter compatibility must be reviewed for the actual hardware. The compliance roadmap is a plan, not proof of conformity.
