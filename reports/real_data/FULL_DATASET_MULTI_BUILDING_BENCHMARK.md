# BDG2 Full-Dataset Multi-Building ML Benchmark & Stress Test Report

**Date:** 2026-09-23 12:49:59 UTC
**Dataset Source:** Building Data Genome 2 (BDG2, Miller et al., 2020)

## 1. Multi-Meter Dataset Files Verification

| File Name | Status | Size (MB) | Details / Channels |
|---|:---:|---:|---|
| `metadata.csv` | **OK** | 0.26 | 32 channels/columns |
| `electricity.csv` | **OK** | 166.17 | 1579 channels/columns |
| `electricity_cleaned.csv` | **OK** | 166.87 | 1579 channels/columns |
| `weather.csv` | **OK** | 18.56 | 10 channels/columns |
| `solar.csv` | **OK** | 0.71 | 6 channels/columns |
| `gas.csv` | **OK** | 19.25 | 178 channels/columns |
| `chilledwater.csv` | **OK** | 75.19 | 556 channels/columns |
| `water.csv` | **OK** | 14.92 | 147 channels/columns |

## 2. Multi-Building Commercial Cohort Neural Network Benchmark

All models trained on calendar year 2016 and evaluated strictly on held-out calendar year 2017.

| Building | Type | Mean kW | Max kW | Test Hours | Train (s) | **R² Score** | **MAE (kW)** | **WAPE (%)** | **P95 Safety Coverage** |
|---|---|---:|---:|---:|---:|:---:|---:|---:|:---:|
| `Wolf_retail_Marcella` | Retail | 6.75 | 20.23 | 8,759 | 1.7s | **0.893** | 0.654 | 9.68% | **99.8%** |
| `Panther_retail_Lester` | Retail | 6.72 | 24.86 | 8,749 | 0.92s | **0.917** | 0.95 | 14.13% | **99.3%** |
| `Panther_retail_Kristina` | Retail | 32.22 | 79.69 | 8,749 | 0.79s | **0.960** | 2.184 | 6.78% | **98.7%** |
| `Panther_retail_Felix` | Retail | 112.96 | 220.96 | 8,750 | 0.93s | **0.970** | 5.563 | 4.92% | **100.0%** |
| `Wolf_retail_Toshia` | Retail | 70.64 | 203.16 | 8,759 | 1.72s | **0.968** | 5.694 | 8.06% | **99.7%** |
| `Fox_food_Francesco` | Food sales and service | 62.81 | 134.19 | 8,758 | 1.13s | **0.923** | 4.537 | 7.22% | **99.3%** |
| `Fox_food_Scott` | Food sales and service | 74.76 | 156.38 | 8,746 | 1.5s | **0.925** | 5.384 | 7.2% | **99.5%** |
| `Hog_food_Morgan` | Food sales and service | 64.1 | 145.26 | 8,760 | 2.12s | **0.959** | 3.71 | 5.79% | **99.8%** |
| `Panther_office_Hannah` | Office | 5.99 | 27.77 | 8,748 | 0.78s | **0.788** | 1.124 | 18.77% | **98.6%** |
| `Panther_lodging_Russell` | Lodging/residential | 39.85 | 83.66 | 8,750 | 0.86s | **0.980** | 1.075 | 2.7% | **100.0%** |

### Cohort Summary Statistics:
- **Average $R^2$ Variance Explained:** **0.928** (92.8%)
- **Average Weighted Error ($WAPE$):** **8.53%**
- **Average P95 Safety Envelope Coverage:** **99.5%**

## 3. Findings & Conclusions

1. **Scalability:** The MLP neural network and tinyML feature engineering successfully scaled across diverse commercial load profiles without memory overflow or numerical instability.
2. **P95 Reliability:** The probabilistic P95 risk envelope consistently achieved >98% coverage across facilities, proving it provides an effective upper bound to prevent peak capacity surcharge violations.
3. **Readiness:** The full dataset is present and validated locally for offline research and baseline generation.