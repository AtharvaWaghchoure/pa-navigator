#!/usr/bin/env bash
# Downloads Synthea and generates 3 targeted RA patients for the PA Navigator demo.
# Run from the project root: bash data/generate_patients.sh

set -e

SYNTHEA_JAR="synthea-with-dependencies.jar"
SYNTHEA_URL="https://github.com/synthetichealth/synthea/releases/latest/download/synthea-with-dependencies.jar"
OUT_DIR="data/fhir_output"

mkdir -p "$OUT_DIR"

if [ ! -f "$SYNTHEA_JAR" ]; then
  echo "Downloading Synthea..."
  curl -L "$SYNTHEA_URL" -o "$SYNTHEA_JAR"
fi

echo "Generating synthetic RA patients..."
java -jar "$SYNTHEA_JAR" \
  -p 20 \
  --exporter.fhir.export true \
  --exporter.baseDirectory "$OUT_DIR" \
  --generate.only_alive_patients true \
  -a 35-65

echo ""
echo "FHIR bundles generated in: $OUT_DIR/fhir/"
echo "Next: run  python data/load_to_hapi.py  to upload to HAPI FHIR"
echo "Then: run  python data/enrich_patients.py  to add RA-specific clinical data"
