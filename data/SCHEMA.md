# Vehicle Inventory Schema

Modeled on the fields Copart actually surfaces on a lot listing (year/make/model,
damage, title, odometer, keys, run & drive, sale location, current bid, etc.),
trimmed to what a search/filter experience needs. This is the contract the
rest of the system is built around: the LLM extracts filters into this shape,
a validation layer checks values against it, and the search layer queries it.

| Field              | Type              | Notes                                                              |
|---------------------|-------------------|---------------------------------------------------------------------|
| lot_id              | str (PK)          | e.g. "45812093"                                                     |
| vin                 | str               | 17-char, synthetic (not a real checksum)                            |
| year                | int               | 1995-2025                                                            |
| make                | str (enum-like)   | validated against a known-makes list                                |
| model               | str               | validated against known models for that make                        |
| trim                | str \| null       |                                                                       |
| body_style          | str               | Sedan, SUV, Pickup, Coupe, Van, Motorcycle, ...                      |
| vehicle_type        | str               | Automobile, Motorcycle, SUV, Truck                                   |
| color               | str               |                                                                       |
| odometer            | int               | miles                                                                |
| odometer_status     | str (enum)        | ACTUAL, NOT_ACTUAL, EXEMPT                                           |
| primary_damage      | str (enum)        | FRONT END, REAR END, SIDE, ALL OVER, WATER/FLOOD, HAIL, VANDALISM, MECHANICAL, BURN, UNDERCARRIAGE, NORMAL WEAR |
| secondary_damage    | str \| null       | same enum, optional                                                  |
| title_type          | str (enum)        | CLEAN, SALVAGE, REBUILT, PARTS ONLY, JUNK                            |
| title_state         | str               | 2-letter state code                                                  |
| loss_type           | str (enum)        | COLLISION, COMPREHENSIVE, THEFT, VANDALISM                           |
| has_keys            | bool              |                                                                       |
| airbags_deployed    | bool              |                                                                       |
| run_and_drive       | str (enum)        | RUNS_DRIVES, START_ONLY, UNKNOWN                                     |
| drivetrain          | str (enum)        | FWD, RWD, AWD, 4WD                                                   |
| fuel_type           | str (enum)        | GAS, DIESEL, HYBRID, ELECTRIC                                        |
| transmission        | str (enum)        | AUTOMATIC, MANUAL                                                    |
| cylinders           | int \| null       |                                                                       |
| current_bid         | float             | USD                                                                  |
| buy_it_now_price    | float \| null     | USD, present on a subset of lots                                    |
| sale_date           | str (ISO date)    |                                                                       |
| yard_name           | str               | e.g. "Dallas"                                                        |
| yard_city           | str               |                                                                       |
| yard_state          | str               | 2-letter                                                             |
| highlights          | str \| null       | comma-separated tags, e.g. "Enhanced Vehicle,Buy It Now"             |

Enum/reference lists (used both for data generation and for the guardrail
layer's validation) live in `generate_data.py` as `MAKES_MODELS`,
`PRIMARY_DAMAGE`, `TITLE_TYPES`, `YARDS`, etc. — the search/filter layer
imports the same constants so "known good values" never drift between the
data and the validator.
