GH_RAW_BASE="https://raw.githubusercontent.com"

## Clean up custom component
path="custom_components/netatmo"
rm ${path}/*.py

## Gather HA integration and modify
GH_ACCOUNT="cgtobi"
GH_REPO="home-assistant"
GH_BRANCH="netatmo_debug_climate"
gh_path="${GH_RAW_BASE}/${GH_ACCOUNT}/${GH_REPO}/${GH_BRANCH}/homeassistant/components/netatmo"
files="__init__.py api.py application_credentials.py camera.py climate.py config_flow.py const.py cover.py data_handler.py device_trigger.py diagnostics.py helper.py light.py media_source.py netatmo_entity_base.py select.py sensor.py switch.py webhook.py"

for file in ${files}; do
  wget ${gh_path}/${file} -O ${path}/${file}
  gsed -i 's/from pyatmo /from \.pyatmo /g' ${path}/${file}
  gsed -i 's/import pyatmo/from . import pyatmo/g' ${path}/${file}
  gsed -i 's/from pyatmo./from .pyatmo./g' ${path}/${file}
done

## Gather pyatmo and modify
path="custom_components/netatmo/pyatmo"
rm ${path}/*.py

GH_ACCOUNT="cgtobi"
GH_REPO="pyatmo"
GH_BRANCH="fix_legrand_modules"
gh_path="${GH_RAW_BASE}/${GH_ACCOUNT}/${GH_REPO}/${GH_BRANCH}/src/pyatmo"
files="__init__.py __main__.py __version__.py account.py auth.py camera.py const.py event.py exceptions.py helpers.py home.py home_coach.py person.py public_data.py py.typed room.py schedule.py thermostat.py weather_station.py"

for file in ${files}; do
  wget ${gh_path}/${file} -O ${path}/${file}
  gsed -i 's/from pyatmo /from \. /g' ${path}/${file}
  gsed -i 's/from pyatmo/from \./g' ${path}/${file}
  gsed -i 's/from \.\./from \./g' ${path}/${file}
done

path="custom_components/netatmo/pyatmo/modules"
rm ${path}/*.py

gh_path="${GH_RAW_BASE}/${GH_ACCOUNT}/${GH_REPO}/${GH_BRANCH}/src/pyatmo/modules"
files="__init__.py base_class.py bticino.py device_types.py idiamant.py legrand.py module.py netatmo.py smarther.py"

for file in ${files}; do
  wget ${gh_path}/${file} -O ${path}/${file}
  gsed -i 's/from pyatmo/from \.\./g' ${path}/${file}
  gsed -i 's/from \.\./from \./g' ${path}/${file}
done
