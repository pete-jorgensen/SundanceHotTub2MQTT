FROM python:alpine as base

WORKDIR /pybalboa

COPY requirements.txt .
RUN pip install -r requirements.txt

ADD pybalboa/*.py ./
ENTRYPOINT ["python3"]
CMD ["-u","app.py"]