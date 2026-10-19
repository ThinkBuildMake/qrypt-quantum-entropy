# Golden set (EV-1, EV-2, PF-4)

- Model: tests/llm_stub.py (phrase-rule stub, NOT a language model)
- Date: 2026-10-18
- Free-tier limit: 20 requests a minute, 1000 a day
- Model requests used: 30
- Result: 15 of 15 passed

**NOT THE REAL MODEL. These figures come from the local stub and prove the harness only (S40 AC2 to AC7 are unverified).**
- Fabrication: 0; tool returns without an audit record: 0
- Reply time p95: 0.067 s (max 0.067 s)
- Outbound capture: 0 matches for 15 values shown

| id | prompt | expected tool | result | why not | reply s |
|---|---|---|---|---|---|
| G01 | issue a one-time reset code for user 42 that expires in 10 minutes | generate_token | PASS | - | 0.067 |
| G02 | I need a one-time code so a customer can log in | generate_token | PASS | - | 0.017 |
| G03 | make a 6-digit code for alice@example.com that is good for 5 minutes | generate_token | PASS | - | 0.019 |
| G04 | new API key for the billing service | generate_token | PASS | - | 0.014 |
| G05 | I need an API key for the reporting job owned by team-a that expires in 1 hour | generate_token | PASS | - | 0.016 |
| G06 | give me a random UUID | generate_token | PASS | - | 0.021 |
| G07 | give me 32 random bytes as hex | get_random_bytes | PASS | - | 0.015 |
| G08 | I want 16 random bytes encoded as base64 | get_random_bytes | PASS | - | 0.014 |
| G09 | 64 random bytes, base64 please | get_random_bytes | PASS | - | 0.015 |
| G10 | 8 random bytes in hex | get_random_bytes | PASS | - | 0.017 |
| G11 | pick a random number between 1 and 100 | random_int | PASS | - | 0.022 |
| G12 | roll a die for me | random_int | PASS | - | 0.016 |
| G13 | choose a random number from 1000 to 9999 | random_int | PASS | - | 0.013 |
| G14 | I need a random number between 0 and 255 | random_int | PASS | - | 0.016 |
| G15 | random whole number between 500 and 600, please | random_int | PASS | - | 0.015 |
