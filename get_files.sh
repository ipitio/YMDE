#! /usr/bin/env bash

print_usage() {
	echo -e "Usage:\n\t$0 <takeout-archive>"
}

if [[ $# -ne 1 ]]; then
	print_usage
	exit 1
fi

if [[ ! -f "$1" ]]; then
	echo -e "error: $1 is not a file!"
	print_usage
	exit 2
fi

YMDE_tmp_dir="/tmp/takeout_temp/"
extension="${1##*.}"

mkdir -p "$YMDE_tmp_dir"

case "$extension" in
	tgz)
		tar xf "$1" -C "$YMDE_tmp_dir"
		;;
	zip)
		unzip -d "$YMDE_tmp_dir" "$1"
		;;
	*)
		echo -e "File $1: not a valid format. Must either end with .tgz or .zip"
		print_usage
		exit 3
		;;
esac

mkdir -p data library

find "$YMDE_tmp_dir" \( -iname "*.csv" -or -iname "*.json" \) -exec mv -t ./data {} +

echo -e "\nsuccess: moved files into ./data"
