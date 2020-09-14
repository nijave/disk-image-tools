FROM docker.io/library/fedora as base

RUN dnf install -y python3-pip 'dnf-command(builddep)' make && dnf builddep -y libguestfs
RUN python3 -m pip install -U pip wheel setuptools auditwheel

FROM base as libguestfs
RUN useradd -m build && mkdir -p /io && chown build /io && chmod 755 /io
USER build
WORKDIR /home/build
RUN python3 -m pip install --user requests

COPY guestfs_install.sh /home/build/
RUN /home/build/guestfs_install.sh

USER root
RUN bash -c "cd /home/build/libguestfs* && make INSTALLDIRS=vendor DESTDIR=/ install"

FROM libguestfs as disk_image
USER build

COPY requirements.txt /home/build
RUN python3 -m pip install --user --upgrade -r requirements.txt --find-links /io

COPY main.py /home/build/
WORKDIR /image
ENTRYPOINT ["python3", "/home/build/main.py"]
CMD []
