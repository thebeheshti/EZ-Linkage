# Evaluation data

The synthetic datasets used to evaluate EZ Linkage, and the script that generated them. Every
person in them is fictional: names, addresses, dates of birth and SSNs were generated with the
[Faker](https://faker.readthedocs.io) library.

| File | Records | Contents |
|---|---|---|
| `file_a.csv` | 10,000 | A clean "registry" file |
| `file_b.csv` | 10,000 | The same kind of records in a different layout, with data-entry errors |
| `ground_truth.csv` | 5,000 | The true matches: which record in File A is the same person as which record in File B |
| `generate_benchmark_data.py` | | The script that produced the three files |

## How the data is built

The script creates a population of 15,000 people. File A holds people 0 to 9,999 and File B holds
people 5,000 to 14,999, so 5,000 people appear in both files (listed in `ground_truth.csv`) and
5,000 in each file have no match.

**File A** (`rec_id, first_name, middle_name, last_name, date_of_birth, ssn, street, city, state,
zip_code, gender`) keeps each field in its own column, with dates as YYYY-MM-DD, SSNs as
123-45-6789 and gender as Male / Female.

**File B** (`rec_id, full_name, dob, social_security, full_address, sex`) uses a different layout:
the full name in one column, dates as MM/DD/YYYY, SSNs without dashes, the address in one string
and sex as M / F. Each record also has up to two errors, each applied with its own probability:

| Error | Probability | What it does |
|---|---|---|
| Name typo | 15% | One character in the first, middle or last name is substituted, swapped, deleted or inserted |
| Date error | 15% | Day and month swapped, the year off by one, or the day off by one or two |
| SSN error | 10% | One digit replaced |
| Address typo | 10% | Two characters in the street substituted, swapped, deleted or inserted |
| Missing value | 7% | The middle name, date of birth, SSN, address or sex left blank |

Record IDs end in `-a` or `-b`, and a person keeps the same number in both files, so
`rec-05000-a` and `rec-05000-b` are the same person.

## Regenerating

```bash
pip install faker pandas
python generate_benchmark_data.py      # writes to a "Datasets for Benchmark" folder in the current directory
```

Running it again gives different names, addresses and dates of birth (they depend on the Faker
version and the date it runs), so use the CSV files in this folder to reproduce the evaluation.
