.PHONY: test demo examples validate up live
test:      ; python -m pytest -v
examples:  ; sh scripts/generate_examples.sh
up:        ; docker compose up -d postgres
demo:      ; docker compose run --rm demo
live:      ; docker compose run --rm pipeline
