# Netatmo component [BETA]

:warning: **Please don't use this unless you know what you are doing. This is not supported nor will it be maintained beyond the purpose of this beta test.**

This repo is a custom component from the beta version of the official HA Netatmo component.

Add this repo as a custom repo in HACS and install it. Remove the old Netatmo integration. Reboot Home Assistant and configure the newly installed netatmo component through the integration page as per the old one. 

## Installation

In order to use the custom component please follow the steps below:
1. Remove the official integration
2. Install HACS
3. Restart HA
4. Add the custom integration repo https://github.com/cgtobi/netatmo_custom
5. Add the Netatmo integration

## What does it bring?

### Multiple Homes coverage selection

In your netatmo account you may have multiple homes, all where supported and imported in your homeassitant instance, and so all their devices and sensors. 
You can now select the homes you want to support in your homeassistant instance (some complex houses may have multiple legrand gateways, hence the need to select multiple covered homes)

Once this integration is properly installed
1. Go to Settings > Devices & Services > Integrations Select the netatmo one (should have the HACS logo)
2. Click Configure
3. If you have multiple Homes, you should see a selector to select the homes to be covered
4. Unfortunately you will have to manually delete the devices and entities not exposed anymore 
    - Settings > Devices & Services > Devices
    - click on each device from the "wrong" homes 
    - In the device info, click teh three dots and delete, don't worry the integration won't let you delete a devices that is in use


### Adding Energy Entities!

We had power entities before, but to use it in homeassitant entity dashboard we had to do a Riemann sum, and well it was really inexact...so now we do have now **sensor.mydevice_energy_sum** goinf straight form the netatmo API, and refined with some power measurement.

There is also two helpers : 
- **sensor.netatmo_global_energy_sensor** 
- **sensor.netatmo_global_energy_sensor_power_adapted**

The first one is really the sum of the energy measures from Netatmo/Legrand, but this APIs being really updated every few hours from Netatmo/Legrand, the second one is introduced and estimates the current energy since the last API update with the power API (updated every 5mn)
For those global sensor we do have also now a new option : the possibility to exclude some meter from those global sum, this is usefull in the case when one of your metter is a global house metter, and you want the global sum to represent only the sum of your loads. 
As above: 
1. Go to Settings > Devices & Services > Integrations Select the netatmo one (should have the HACS logo)
2. Click Configure
3. If you do have some meters they will appear here, select the oones you want to exclude from the global sensors above

### Probably some new bugs and some fixes too :)

What may be better now:

- Handling of API throttling is more exact we may have less lacunar data now, and better compliance with netatmo rate limites
- The schdules : now are limited to temperature schedules
- Some device that was not exposing power (and now energy) are now exposing it
