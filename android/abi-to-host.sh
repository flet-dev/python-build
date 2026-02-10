case ${abi:?} in
    arm64-v8a)
        HOST=aarch64-linux-android
        ;;
    x86_64)
        HOST=x86_64-linux-android
        ;;
    *)
        echo "Unknown ABI: '$abi'"
        exit 1
        ;;
esac
