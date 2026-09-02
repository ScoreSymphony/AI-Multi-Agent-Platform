from pathlib import Path

path = Path("tests/test_kernel_consistency.py")
text = path.read_text()
old = '''                subject_type="step",
                subject_id=new_id("step"),
'''
new = '''                subject_type="step",
                subject_id=planned_step_ids(k, task_id)[0],
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one remaining fabricated step subject, found {text.count(old)}")
path.write_text(text.replace(old, new, 1))
