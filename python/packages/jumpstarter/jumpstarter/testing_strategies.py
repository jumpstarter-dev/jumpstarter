from hypothesis import strategies as st

label_key: st.SearchStrategy[str] = st.from_regex(r"[a-zA-Z][a-zA-Z0-9_./-]{0,30}", fullmatch=True)
label_value: st.SearchStrategy[str] = st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,30}", fullmatch=True)
