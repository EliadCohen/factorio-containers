for i in $(podman images --sort repository | grep "<none>" | awk  '{ print $3 }'); do podman image rm $i; done