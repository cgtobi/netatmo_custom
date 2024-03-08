GH_RAW_BASE="https://raw.githubusercontent.com"


## Gather pyatmo and modify
path="custom_components/netatmo/pyatmo"
rm ${path}/*.py
rm ${path}/*.typed

GH_ACCOUNT="tmenguy"
GH_REPO="pyatmo"
GH_BRANCH="development"
gh_path="${GH_RAW_BASE}/${GH_ACCOUNT}/${GH_REPO}/${GH_BRANCH}/src/pyatmo"
files="__init__.py account.py auth.py const.py event.py exceptions.py helpers.py home.py person.py py.typed room.py schedule.py"

for file in ${files}; do
  wget ${gh_path}/${file} -O ${path}/${file}
  gsed -i 's/from pyatmo /from \. /g' ${path}/${file}
  gsed -i 's/from pyatmo/from \./g' ${path}/${file}
  gsed -i 's/from \.\./from \./g' ${path}/${file}
done

path="custom_components/netatmo/pyatmo/modules"
rm ${path}/*.py

gh_path="${GH_RAW_BASE}/${GH_ACCOUNT}/${GH_REPO}/${GH_BRANCH}/src/pyatmo/modules"
files="__init__.py base_class.py bticino.py device_types.py idiamant.py legrand.py module.py netatmo.py smarther.py somfy.py"

for file in ${files}; do
  wget ${gh_path}/${file} -O ${path}/${file}
  gsed -i 's/from pyatmo/from \.\./g' ${path}/${file}
  gsed -i 's/from \.\./from \./g' ${path}/${file}
done
