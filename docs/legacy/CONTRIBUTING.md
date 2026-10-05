# Contributing to Behind-the-Meter EMS

Thank you for your interest in contributing to the **Behind-the-Meter Energy Management System (EMS)**! This project provides open-source, non-invasive energy monitoring and Greek electricity tariff tracking for small and medium-sized commercial enterprises.

---

## 1. Development Workflow

### Prerequisites
- Python 3.11+ or Python 3.12
- Git
- PlatformIO Core (for ESP32 firmware development)

### Setting Up the Environment
```bash
git clone https://github.com/DimThanasoulias/Behind-the-Meter-EMS.git
cd Behind-the-Meter-EMS

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies in editable mode
pip install -e ".[dev]"
```

---

## 2. Code Quality & Standards

We enforce strict linting, type validation, and test coverage:

- **Formatting & Linting:**
  ```bash
  ruff check .
  ruff format --check .
  ```
- **Test Suite Execution:**
  ```bash
  pytest -v
  ```
  Ensure all 333+ automated tests pass with 100% success rate.
- **Standalone E2E Runner:**
  ```bash
  python scripts/run_e2e_verification.py --profile bakery
  ```
  Must pass all 9/9 verification checks in under 30 seconds.

---

## 3. Pull Request Guidelines

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/my-new-feature
   ```
2. Write clean, self-documenting code with type annotations and docstrings.
3. If modifying the Greek tariff engine, reference relevant Hellenic regulatory documents (Law 5068/2023, RAEWW decisions, DEDDIE grid codes).
4. If modifying firmware, verify that `platformio.ini` builds cleanly for `esp32dev` with zero compiler warnings.
5. Submit your PR with a clear summary of changes, rationale, and test results.
