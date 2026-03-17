clean:
	bash clean_images.sh

build:
	bash build.sh

update:
	podman build --no-cache -f ./Container/Containerfile -t factorio-headless:latest
