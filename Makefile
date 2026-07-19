.PHONY: test

test:
	uv run --python 3.13 --with-requirements requirements.txt --with 'mcp[cli]>=1.27.0' --with pytest pytest -q
