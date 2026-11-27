if grep -q bad "$1"; then
  echo 'A.rule: violation'
  exit 1
fi
