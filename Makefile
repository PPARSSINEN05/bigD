.PHONY: setup seed run scheduler test clean

setup:
	python3 -m app.seed --reset

seed:
	python3 -m app.seed

run:
	python3 -m app.server --migrate --host 127.0.0.1 --port 8000

scheduler:
	python3 -m app.scheduler

test:
	python3 -m unittest discover -s tests -v

clean:
	rm -rf data
