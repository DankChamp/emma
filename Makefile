.PHONY: setup run dev cli gui clean

setup:
	./setup.sh

run:
	./run.sh

dev:
	./run.sh --dev

gui:
	./run_gui.sh

cli:
	@echo "Usage: make cli ARGS='task list'"
	./emma $(ARGS)

clean:
	rm -rf .venv data/*.db __pycache__ core/**/__pycache__ api/**/__pycache__
