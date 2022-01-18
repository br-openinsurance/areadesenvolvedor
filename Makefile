
SWAGGER_DIR := documentation/source/files/swagger
OUT_DIR := documentation/source/includes/partials
WIDDERSHINS_OPTS := --language_tabs "javascript:JavaScript:request" "python:Python:request" "java:Java::request" --omitHeader --summary --httpsnippet

widdershins:
	mkdir -p ${OUT_DIR}

	widdershins ${SWAGGER_DIR}/metrics.yaml              -o ${OUT_DIR}/_metrics.md.erb            ${WIDDERSHINS_OPTS}
	#widdershins ${SWAGGER_DIR}/status_outage.yaml        -o ${OUT_DIR}/_status_outage.md.erb      ${WIDDERSHINS_OPTS}
	widdershins ${SWAGGER_DIR}/canais_atendimento.yaml   -o ${OUT_DIR}/_canais_atendimento.md.erb ${WIDDERSHINS_OPTS}
	widdershins ${SWAGGER_DIR}/auto-insurance.yaml       -o ${OUT_DIR}/_auto.md.erb               ${WIDDERSHINS_OPTS}
	widdershins ${SWAGGER_DIR}/capitalization-title.yaml -o ${OUT_DIR}/_capitalization.md.erb     ${WIDDERSHINS_OPTS}
	widdershins ${SWAGGER_DIR}/home-insurance.yaml       -o ${OUT_DIR}/_home.md.erb               ${WIDDERSHINS_OPTS}
	widdershins ${SWAGGER_DIR}/life-pension.yaml         -o ${OUT_DIR}/_life.md.erb               ${WIDDERSHINS_OPTS}
	widdershins ${SWAGGER_DIR}/pension-plan.yaml         -o ${OUT_DIR}/_pension.md.erb            ${WIDDERSHINS_OPTS}
	widdershins ${SWAGGER_DIR}/person.yaml               -o ${OUT_DIR}/_person.md.erb             ${WIDDERSHINS_OPTS}

validate:
	swagger-cli validate ${SWAGGER_DIR}/metrics.yaml
	#swagger-cli validate ${SWAGGER_DIR}/status_outage.yaml
	swagger-cli validate ${SWAGGER_DIR}/canais_atendimento.yaml

	swagger-cli validate ${SWAGGER_DIR}/auto-insurance.yaml
	swagger-cli validate ${SWAGGER_DIR}/capitalization-title.yaml
	swagger-cli validate ${SWAGGER_DIR}/home-insurance.yaml
	swagger-cli validate ${SWAGGER_DIR}/life-pension.yaml
	swagger-cli validate ${SWAGGER_DIR}/pension-plan.yaml
	swagger-cli validate ${SWAGGER_DIR}/person.yaml

	spectral lint ${SWAGGER_DIR}/metrics.yaml
	#spectral lint ${SWAGGER_DIR}/status_outage.yaml
	spectral lint ${SWAGGER_DIR}/canais_atendimento.yaml

	spectral lint ${SWAGGER_DIR}/auto-insurance.yaml
	spectral lint ${SWAGGER_DIR}/capitalization-title.yaml
	spectral lint ${SWAGGER_DIR}/home-insurance.yaml
	spectral lint ${SWAGGER_DIR}/life-pension.yaml
	spectral lint ${SWAGGER_DIR}/pension-plan.yaml
	spectral lint ${SWAGGER_DIR}/person.yaml
