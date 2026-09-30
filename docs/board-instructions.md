# Board paper instructions

Active subject presets have maintained, numbered candidate directions. CBSE and ICSE directions are adapted from the official sample/specimen papers in the catalog, in our own wording. ISC retains its existing full candidate directions, with the Biology pencil requirement added. Specimen page counts and examiner-only directions are excluded because they do not describe a generated paper.

## Selection and persistence

- For built-in subject presets, use the catalog's current candidate directions, even when an older compiled snapshot or model response contains a summary.
- A teacher who changes the Instructions field sets `instructions_edited: true`. That saved text takes precedence in the paper view, completion checkpoints, PDF and DOCX.
- Saving another field does not turn default directions into a teacher override.
- Existing unmarked instructions are treated as generated content. Historical teacher edits cannot be distinguished from model text because earlier versions did not record provenance.
- Custom blueprint snapshots keep their supplied candidate directions. Papers without a blueprint keep their saved instructions.
- Loading a paper refreshes its presentation without writing to storage or regenerating questions.

The catalog builds the editable setup prompt from the same candidate directions and structured section contract. It includes question ranges, formats, subpart marks and internal choices. This replaces abbreviated prompts and avoids maintaining a second, contradictory description of the paper structure.

## Official sources checked

The catalog uses CBSE 2026–27 and CISCE 2026 presets. This change updates their instructions; it does not migrate their question allocation contracts to another session.

- [CBSE X Mathematics Standard](https://cbseacademic.nic.in/web_material/SQP/ClassX_2026_27/MathsStandard-SQP.pdf)
- [CBSE X Mathematics Basic](https://cbseacademic.nic.in/web_material/SQP/ClassX_2026_27/MathsBasic-SQP.pdf)
- [CBSE X Science](https://cbseacademic.nic.in/web_material/SQP/ClassX_2026_27/Science-SQP.pdf)
- [CBSE XII Mathematics](https://cbseacademic.nic.in/web_material/SQP/ClassXII_2026_27/Maths-SQP.pdf)
- [CBSE XII Physics](https://cbseacademic.nic.in/web_material/SQP/ClassXII_2026_27/Physics-SQP.pdf)
- [CBSE XII Chemistry](https://cbseacademic.nic.in/web_material/SQP/ClassXII_2026_27/Chemistry-SQP.pdf)
- [CBSE XII Biology](https://cbseacademic.nic.in/web_material/SQP/ClassXII_2026_27/Biology-SQP.pdf)
- [ICSE Mathematics](https://cisce.org/wp-content/uploads/2025/11/ICSE-2026-SPECIMEN-511-MAT.pdf)
- [ICSE Physics](https://cisce.org/wp-content/uploads/2025/11/ICSE-2026-SPECIMEN-521-SCI1.pdf)
- [ICSE Chemistry](https://cisce.org/wp-content/uploads/2025/11/ICSE-2026-SPECIMEN-522-SCI2.pdf)
- [ICSE Biology](https://cisce.org/wp-content/uploads/2025/11/ICSE-2026-SPECIMEN-523-SCI3.pdf)
- [ISC Mathematics](https://cisce.org/wp-content/uploads/2025/08/860-MATHEMATICS-SQP-AK.pdf)
- [ISC Physics](https://cisce.org/wp-content/uploads/2025/08/861A-PHYSICS-PAPER-1-SQP-AK.pdf)
- [ISC Chemistry](https://cisce.org/wp-content/uploads/2025/08/862A-CHEMISTRY-PAPER-1-SQP-AK.pdf)
- [ISC Biology](https://cisce.org/wp-content/uploads/2025/08/863A-BIOLOGY-PAPER-1-SQP-AK.pdf)

## Regression coverage

`test_printable_instructions.py` checks active preset coverage, detailed prompt content, old snapshot upgrades, board-specific rules, and PDF/DOCX output. `test_completion_api.py` checks detail reads and explicit-edit provenance. `test_paper_completion.py` checks every publication through a repair cycle. The frontend instruction formatter retains numbering through 10, 11 and 12.
