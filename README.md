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

In you netatmo account you may have multiple homes, all where covered and imported in your homeassitant instance. 
You can now select the ones you want to cover (some complex houses may have multiple legrand gateways, hence the need to select multiple covered homes)

Once this integration is properly installed
1. Go tot Settings > Devices & Services > Integrations Select the netatmo one (should have the HACS logo)
2. Click Configure
3. If you have multiple Homes, you should see a selector to select the homes to be covered
4. Unfortunately you will have to manually delete the devices and entities not exposed anymore 
    - Settings > Devices & Services > Devices
    - click on each device from the "wrong" homes 
    - In the device info, click teh three dots and delete, don't worry the integration won't let you delete a devices that is in use


### Adding Energy Entities!

We had power entites before, but to use it in homeassitant entity dashboard we had to do a Riemann sum, and well it was really inexact...so now we do have sensor.mydevice_energy_sum
Something to note : the first day measure you will get will be pretty high and wrong : normal, the energy measure are a sum, and the start is a few week before : so the energy dashboard, without any history wil think that the first day saw a big energy bump because it represents few weeks of consumption, but the day after should be correct.


### Probably some new bugs and some fixes too :)