# Container image for the ZIP / state / federal district validation Lambda.
#
# Uses the official AWS Lambda Python base image so the image is directly
# deployable as a container-image Lambda. pandas/openpyxl have compiled parts,
# which is why a container image is simpler here than a zip + layers.
FROM public.ecr.aws/lambda/python:3.12

# Install Python dependencies into the Lambda task root.
# (boto3 is already present in the base image; installing the pinned version
# from requirements.txt keeps local and image environments identical.)
COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy the application source. The ZIP reference workbook is intentionally NOT
# bundled — it is fetched from S3 at runtime via ZIP_LOOKUP_S3_URI (see
# .dockerignore and DEPLOY.md).
COPY nb_insights.py pull_zip_state_federal.py ${LAMBDA_TASK_ROOT}/

# Lambda calls <module>.<function>. handler() lives in pull_zip_state_federal.
CMD ["pull_zip_state_federal.handler"]
