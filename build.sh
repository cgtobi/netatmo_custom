GH_RAW_BASE="https://raw.githubusercontent.com/cgtobi"

## Gather HA integration and modify
path="custom_components/netatmo"
rm ${path}/*.py

GH_REPO="home-assistant"
GH_BRANCH="netatmo_shutters"
gh_path="${GH_RAW_BASE}/${GH_REPO}/${GH_BRANCH}/homeassistant/components/netatmo"
files="__init__.py const.py light.py cover.py api.py data_handler.py media_source.py switch.py camera.py device_trigger.py netatmo_entity_base.py climate.py diagnostics.py select.py webhook.py config_flow.py helper.py sensor.py"

for file in ${files}; do
  echo ${file}
  wget ${gh_path}/${file} -O ${path}/${file}
  gsed -i 's/import pyatmo/from . import pyatmo/g' ${path}/${file}
  gsed -i 's/from pyatmo./from .pyatmo./g' ${path}/${file}
done

## Gather pyatmo and modify
path="custom_components/netatmo/pyatmo"
rm ${path}/*.py

GH_REPO="pyatmo"
GH_BRANCH="20211120"
gh_path="${GH_RAW_BASE}/${GH_REPO}/${GH_BRANCH}/src/pyatmo"
files="__init__.py account.py exceptions.py room.py __main__.py auth.py helpers.py person.py schedule.py camera.py home.py public_data.py thermostat.py __version__.py const.py home_coach.py py.typed weather_station.py"

for file in ${files}; do
  wget ${gh_path}/${file} -O ${path}/${file}
  gsed -i 's/from pyatmo/from ./g' ${path}/${file}
  gsed -i 's/from ../from ./g' ${path}/${file}
done

path="custom_components/netatmo/pyatmo/modules"
rm ${path}/*.py

gh_path="${GH_RAW_BASE}/${GH_REPO}/${GH_BRANCH}/src/pyatmo/modules"
files="__init__.py base_class.py device_types.py legrand.py netatmo.py bticino.py idiamant.py module.py"

for file in ${files}; do
  wget ${gh_path}/${file} -O ${path}/${file}
  gsed -i 's/from pyatmo/from \.\./g' ${path}/${file}
  gsed -i 's/from \.\./from \./g' ${path}/${file}
done