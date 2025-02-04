FROM fedora:latest
RUN dnf -y update
RUN dnf -y install wget
RUN dnf -y clean all
RUN useradd -u 1001 factorio
ENV HOME /home/factorio/
WORKDIR /home/factorio
RUN curl -JL https://factorio.com/get-download/stable/headless/linux64 -o /home/factorio/factorio-headless-latest.tar.gz
RUN tar -xf  factorio-headless-latest.tar.gz && rm -f factorio-headless-latest.tar.gz
COPY saves/server-adminlist.json /home/factorio/factorio/data/
COPY server-scripts/start_server.sh /home/factorio/factorio/
RUN chown -R factorio:factorio /home/factorio/factorio/
USER factorio
WORKDIR /home/factorio/factorio
EXPOSE 34197/udp 34196/udp
CMD /home/factorio/factorio/start_server.sh
