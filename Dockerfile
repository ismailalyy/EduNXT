FROM python:3.12-slim

# WeasyPrint needs Pango; DejaVu fonts match the PDF templates.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY edunxt ./edunxt
COPY data ./data
RUN pip install --no-cache-dir . && mkdir -p /app/var

# Mount /app/var to keep the database and generated PDFs, and /app/data so the university and
# scholarship lists the Research Agent updates are saved in your own folder:
#   docker run --env-file .env -v "%cd%\data:/app/data" -v "%cd%\var:/app/var" ... edunxt
ENV DATABASE_URL=sqlite:////app/var/edunxt.db \
    OUTPUT_DIR=/app/var/out \
    PARTNERS_CSV=/app/data/partners.csv \
    SCHOLARSHIPS_CSV=/app/data/scholarships.csv
EXPOSE 8000
CMD ["python", "-m", "edunxt", "serve", "--port", "8000"]
