# Netatmo component [BETA]

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

We had power entities before, but to use it in homeassitant entity dashboard we had to do a Riemann sum, and well it was really inexact...so now we do have now **sensor.mydevice_energy** going straight form the netatmo API, and refined with some power measurement.

### Adding Support for the Legrand Ecocounter ((NLE) with its Water and Gaz sensors

### Probably some new bugs and some fixes too :)

What may be better now:

- Handling of API throttling is more exact we may have less lacunar data now, and better compliance with netatmo rate limites
- Some device that was not exposing power (and now energy) are now exposing it
