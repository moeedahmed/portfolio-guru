#!/usr/bin/env python3
"""Test CBD extraction without browser. Run: python test_extraction.py"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", ""))

from extractor import extract_cbd_data
import json

TEST_CASE = """
I saw a 67 year old man in resus last night. He came in with sudden onset chest pain,
8/10, radiating to his left arm, started about 2 hours before arrival.
He was pale and diaphoretic. I was the FY2 on shift, worked with the SpR who was
in the department but supervising from a distance. I took the history, examined him,
requested ECG and troponin, spotted the STEMI on ECG and immediately called the SpR
who activated the cath lab. I stayed with the patient while we waited for the team.
The learning point for me was the importance of quick ECG interpretation in chest pain —
I need to be faster at spotting STEMI patterns. The consultant Dr. Ahmed was on call.
"""

if __name__ == "__main__":
    print("Testing CBD extraction...")
    result = extract_cbd_data(TEST_CASE)
    print(json.dumps(result.model_dump(), indent=2))
    print("\n✅ Extraction successful")
