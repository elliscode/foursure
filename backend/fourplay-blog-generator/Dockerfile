FROM public.ecr.aws/lambda/python:3.14

# Install dependencies into /opt/python (official layer for dependencies)
COPY pyproject.toml .
RUN pip install . -t /opt/python

# Copy code into runtime directory
COPY lambda_function.py ${LAMBDA_TASK_ROOT}/

CMD ["lambda_function.lambda_handler"]
