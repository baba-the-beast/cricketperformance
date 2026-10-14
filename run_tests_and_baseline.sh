#!/bin/bash
source venv/bin/activate
echo "--- BASELINE AUDIT ---" > baseline_report.txt
echo "Python version: $(python3 --version)" >> baseline_report.txt
echo "OS: macOS" >> baseline_report.txt
echo "Dependencies:" >> baseline_report.txt
pip list >> baseline_report.txt
echo "DB Path: data/processed/cricket.db" >> baseline_report.txt
echo "DB Size: $(ls -lh data/processed/cricket.db | awk '{print $5}')" >> baseline_report.txt
echo "Raw data: data/raw (maybe empty, but ipl data is in data/ipl/)" >> baseline_report.txt
echo "Total test files: $(find tests -name '*.py' | wc -l)" >> baseline_report.txt
echo "--- PYTEST RESULTS ---" >> baseline_report.txt
python3 -m pytest tests > pytest_output.txt 2>&1
echo "Tests Passed: $(grep -oE '[0-9]+ passed' pytest_output.txt | tail -n 1)" >> baseline_report.txt
echo "Tests Failed: $(grep -oE '[0-9]+ failed' pytest_output.txt | tail -n 1)" >> baseline_report.txt
cat pytest_output.txt >> baseline_report.txt
