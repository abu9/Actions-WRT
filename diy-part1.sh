#!/bin/bash
#
# Copyright (c) 2019-2020 P3TERX <https://p3terx.com>
#
# This is free software, licensed under the MIT License.
# See /LICENSE for more information.
#
# https://github.com/P3TERX/Actions-OpenWrt
# File name: diy-part1.sh
# Description: OpenWrt DIY script part 1 (Before Update feeds)
#

# Uncomment a feed source
#sed -i 's/^#\(.*helloworld\)/\1/' feeds.conf.default

# Add a feed source
# echo 'src-git kenzojell https://github.com/kenzok8/jell' >>feeds.conf.default

function git_sparse_clone() (
	branch="$1" rurl="$2" localdir="$3" && shift 3
	git clone -b "$branch" --depth 1 --filter=blob:none --sparse "$rurl" "$localdir"
	cd "$localdir" || exit
	git sparse-checkout init --cone
	git sparse-checkout set "$@"
	mv -n "$@" ../
	cd ..
	rm -rf "$localdir" 
	)

function git_clone() (
	git clone --depth 1 --filter=blob:none "$1"
	)

custom_feed_path="$PWD/customfeed"
[[ -s feeds.conf.default ]] && sed -i "1isrc-link custom $custom_feed_path" feeds.conf.default

mkdir "$custom_feed_path" && cd "$_" || exit
git_sparse_clone master "https://github.com/immortalwrt/packages" "immpkgs" lang/perl #fix perl compile issue with external-toolchain
git_sparse_clone master "https://github.com/immortalwrt/luci" "immlucipkgs" applications/luci-app-filebrowser

git_clone https://github.com/tty228/luci-app-wechatpush 
git_clone https://github.com/stevenjoezhang/luci-app-adguardhome
git_clone https://github.com/gSpotx2f/luci-app-log


case $REPO_URL in
    immortalwrt/immortalwrt)
        git apply "${GITHUB_WORKSPACE}"/patchs/0001-fix-default-settings-depends.patch
		git_sparse_clone master "https://github.com/openwrt/packages" "officialpkgs" libs/libffi 		#fix libffi compile issue with external-toolchain
        ;;
esac
