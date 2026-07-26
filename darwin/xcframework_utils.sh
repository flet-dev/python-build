archs=("iphoneos.arm64" "iphonesimulator.arm64" "iphonesimulator.x86_64")

dylib_ext=so

# Build-provenance values that Xcode stamps into every framework Info.plist it
# produces (DT* / BuildMachineOSBuild). The frameworks here are assembled by hand
# from lib-dynload .so's, so without this they carry none of them and the bundle
# doesn't look like it was built by Xcode at all -- which is the most substantive
# difference we could find between our _ssl/_hashlib frameworks and third-party
# OpenSSL xcframeworks that Apple's App Store scan accepts. See flet-dev/flet#6724.
#
# Resolved once per run: identical for every framework built from the same SDK.
sdk_metadata_init() {
    [ -n "${sdk_metadata_ready:-}" ] && return

    build_machine_os_build=$(sw_vers -buildVersion)
    sdk_version=$(xcrun --sdk iphoneos --show-sdk-version)
    sdk_build=$(xcrun --sdk iphoneos --show-sdk-build-version)
    xcode_build=$(xcodebuild -version | sed -n 's/^Build version //p')

    # DTXcode is the Xcode version with the separators dropped, as major(2) +
    # minor(1) + patch(1): 26.5 -> "2650", 16.2 -> "1620", 9.4.1 -> "0941".
    local xcode_version major minor patch
    xcode_version=$(xcodebuild -version | sed -n '1s/^Xcode //p')
    IFS=. read -r major minor patch <<< "$xcode_version"
    dt_xcode=$(printf '%02d%d%d' "$major" "${minor:-0}" "${patch:-0}")

    sdk_metadata_ready=1
}

create_plist() {
    name=$1
    identifier=$2
    plist_file=$3
    # "iphoneos" (default) or "iphonesimulator". The previous version hardcoded
    # iPhoneOS for both slices, which mislabelled the simulator framework.
    platform=${4:-iphoneos}

    sdk_metadata_init

    local supported_platform
    if [ "$platform" = "iphonesimulator" ]; then
        supported_platform=iPhoneSimulator
    else
        supported_platform=iPhoneOS
    fi

    cat > $plist_file << PLIST_TEMPLATE
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>BuildMachineOSBuild</key>
	<string>$build_machine_os_build</string>
	<key>CFBundleDevelopmentRegion</key>
	<string>en</string>
	<key>CFBundleName</key>
	<string>$name</string>
	<key>CFBundleExecutable</key>
	<string>$name</string>
	<key>CFBundleIdentifier</key>
	<string>$identifier</string>
	<key>CFBundleInfoDictionaryVersion</key>
	<string>6.0</string>
	<key>CFBundlePackageType</key>
	<string>FMWK</string>
	<key>CFBundleShortVersionString</key>
	<string>1.0</string>
	<key>CFBundleSupportedPlatforms</key>
	<array>
		<string>$supported_platform</string>
	</array>
	<key>CFBundleVersion</key>
	<string>1</string>
	<key>DTCompiler</key>
	<string>com.apple.compilers.llvm.clang.1_0</string>
	<key>DTPlatformBuild</key>
	<string>$sdk_build</string>
	<key>DTPlatformName</key>
	<string>$platform</string>
	<key>DTPlatformVersion</key>
	<string>$sdk_version</string>
	<key>DTSDKBuild</key>
	<string>$sdk_build</string>
	<key>DTSDKName</key>
	<string>$platform$sdk_version</string>
	<key>DTXcode</key>
	<string>$dt_xcode</string>
	<key>DTXcodeBuild</key>
	<string>$xcode_build</string>
	<key>MinimumOSVersion</key>
	<string>13.0</string>
	<key>UIDeviceFamily</key>
	<array>
		<integer>1</integer>
		<integer>2</integer>
	</array>
PLIST_TEMPLATE

    # Device slice only: the simulator framework is a fat arm64/x86_64 binary, so
    # it can't claim an arm64 device capability.
    if [ "$platform" != "iphonesimulator" ]; then
        cat >> $plist_file << PLIST_CAPABILITIES
	<key>UIRequiredDeviceCapabilities</key>
	<array>
		<string>arm64</string>
	</array>
PLIST_CAPABILITIES
    fi

    cat >> $plist_file << PLIST_TAIL
</dict>
</plist>
PLIST_TAIL
}

# convert lib-dynloads to xcframeworks
create_xcframework_from_dylibs() {
    iphone_dir=$1
    simulator_arm64_dir=$2
    simulator_x86_64_dir=$3
    dylib_relative_path=$4
    origin_prefix=$5
    out_dir=$6

    tmp_dir=$(mktemp -d)
    pushd -- "${tmp_dir}" >/dev/null

    echo "Creating framework for $dylib_relative_path"
    dylib_without_ext=$(echo $dylib_relative_path | cut -d "." -f 1)
    framework=$(echo $dylib_without_ext | tr "/" ".")
    framework_identifier=${framework//_/-}
    while [[ $framework_identifier == -* ]]; do
        framework_identifier=${framework_identifier#-}
    done
    framework_identifier=${framework_identifier:-framework}

    # creating "iphoneos" framework
    fd=iphoneos/$framework.framework
    mkdir -p $fd
    mv "$iphone_dir/$dylib_relative_path" $fd/$framework
    echo "Frameworks/$framework.framework/$framework" > "$iphone_dir/$dylib_without_ext.fwork"
    install_name_tool -id @rpath/$framework.framework/$framework $fd/$framework
    create_plist $framework "org.python.$framework_identifier" $fd/Info.plist iphoneos
    echo "$origin_prefix/$dylib_without_ext.fwork" > $fd/$framework.origin

    # copy privacy manifest if any
    if [ -f "$iphone_dir/$dylib_without_ext.xcprivacy" ]; then
        cp "$iphone_dir/$dylib_without_ext.xcprivacy" "$fd/PrivacyInfo.xcprivacy"
    fi

    # creating "iphonesimulator" framework
    fd=iphonesimulator/$framework.framework
    mkdir -p $fd
    lipo -create \
        "$simulator_arm64_dir/$dylib_without_ext".*.$dylib_ext \
        "$simulator_x86_64_dir/$dylib_without_ext".*.$dylib_ext \
        -output $fd/$framework
    rm "$simulator_arm64_dir/$dylib_without_ext".*.$dylib_ext
    rm "$simulator_x86_64_dir/$dylib_without_ext".*.$dylib_ext
    install_name_tool -id @rpath/$framework.framework/$framework $fd/$framework
    create_plist $framework "org.python.$framework_identifier" $fd/Info.plist iphonesimulator
    echo "$origin_prefix/$dylib_without_ext.fwork" > $fd/$framework.origin

    # copy privacy manifest if any
    if [ -f "$iphone_dir/$dylib_without_ext.xcprivacy" ]; then
        mv "$iphone_dir/$dylib_without_ext.xcprivacy" "$fd/PrivacyInfo.xcprivacy"
    fi

    # merge frameworks info xcframework
    xcodebuild -create-xcframework \
        -framework "iphoneos/$framework.framework" \
        -framework "iphonesimulator/$framework.framework" \
        -output $out_dir/$framework.xcframework

    # cleanup
    popd >/dev/null
    rm -rf "${tmp_dir}" >/dev/null
}
