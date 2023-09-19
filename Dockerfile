FROM ubuntu:20.04

RUN apt update
RUN echo "tzdata tzdata/Areas select America" | debconf-set-selections
RUN echo "tzdata tzdata/Zones/America select New_York" | debconf-set-selections
RUN DEBIAN_FRONTEND=noninteractive apt install -y python3 python3-pip fuse libfuse2 graphviz libgraphviz-dev zsh git curl wget vim
RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" -y
RUN pip3 install repeatfs
RUN repeatfs generate