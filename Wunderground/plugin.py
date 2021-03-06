###
# Copyright (c) 2016, Johannes Löthberg
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

###

import json
from datetime import datetime
from urllib.parse import urlencode, urlunparse

import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks
try:
    from supybot.i18n import PluginInternationalization
    _ = PluginInternationalization('Wunderground')
except ImportError:
    # Placeholder that allows to run the plugin on a bot
    # without the i18n module
    _ = lambda x: x


def retrying_get_url(url, tries_left=0):
    try:
        return utils.web.getUrl(url, timeout=5)
    except utils.web.Error as e:
        if tries_left <= 1:
            raise
        return retrying_get_url(url, tries_left=tries_left-1)

class Wunderground(callbacks.Plugin):
    """Queries wundeground.com for weather forecasts"""
    threaded = True

    conditionsApiBase = 'https://api.wunderground.com/api/{}/conditions/q/'

    def weather(self, irc, msg, args, optlist, loc):
        """[--any-featureclass] [--station <id>] | [--airport <code>] | [<location>]"""
        key = self.registryValue('key')

        opts = dict(optlist)

        if opts.get('station', False):
            query = 'pws:{}'.format(loc)
        elif opts.get('airport', False):
            query = loc
        else:
            defaultLocation = self.userValue('defaultLocation', msg.prefix)

            if not loc and not defaultLocation:
                irc.error('No location given and no default location set')
                return

            if not loc:
                loc = defaultLocation

            location = self.lookup_location(location=loc,
                    any_featureclass=opts.get('any-featureclass', False))
            if not location:
                irc.error('''Could not look up location '{}'. Does that place even exist?'''
                          .format(loc))
                return
            elif 'error' in location:
                irc.error('''Could not look up location: '{}'.'''
                         .format(location['error']))

            query = '{},{}'.format(location['lat'], location['lng'])

        (condition, error) = self.get_current_observation(irc, key, query)
        if error:
            if 'description' in error:
                irc.error('wunderground: {}'.format(error['description']))
            elif opts.get('station', False) and error.get('type') == 'Station:OFFLINE':
                irc.error('''Specified station is offline or doesn't exist.''')
        else:
            irc.reply(u' | '.join(self.format_current_observation(condition)))

    weather = wrap(weather, [getopts({'any-featureclass': '', 'station': '', 'airport': ''}),
                             optional('text')])


    def defaultlocation(self, irc, msg, args, location):
        """[<location>]

        Get or set the default weather location."""

        if location:
            self.setUserValue('defaultLocation', msg.prefix,
                              location, ignoreNoUser=True)
            irc.reply('Default location set to "{}"'.format(location))
        else:
            loc = self.userValue('defaultLocation', msg.prefix)
            if loc:
                irc.reply('Default location is "{}"'.format(loc))
            else:
                irc.reply('No default location set')

    defaultlocation = wrap(defaultlocation, [optional('text')])


    def lookup_location(self, location, any_featureclass=False):
        username = self.registryValue('geonamesUsername')

        query_parameters = {
            'q': location,
            'username': username,
        }
        if not any_featureclass:
            query_parameters['featureClass'] = 'P'

        url = urlunparse(('http', 'api.geonames.org', '/searchJSON', None,
            urlencode(query_parameters), None))

        try:
            data = retrying_get_url(url, 3)
        except utils.web.Error as e:
            return

        data = json.loads(data.decode('utf-8'))

        if 'totalResultsCount' not in data and 'status' in data:
            return { 'error': data['status'].get('message', 'unknown error') }

        if data['totalResultsCount'] == 0:
            return {}
        else:
            return data['geonames'][0]


    def get_current_observation(self, irc, key, query):
        url = self.conditionsApiBase.format(utils.web.urlquote(key))
        url += utils.web.urlquote(query) + '.json'

        try:
            data = retrying_get_url(url, 3)
        except utils.web.Error as e:
            irc.error(_('Failed to get observation data: {}').format(e))
            return (None, "Failed")

        data = json.loads(data.decode('utf-8'))

        if 'current_observation' in data:
            observation = data['current_observation']
            return (observation, None)

        if 'results' in data['response']:
            query = '{}.json'.format(data['response']['results'][0]['l'])
            return self.get_current_observation(irc, key, query)

        return (None, data['response']['error'])


    def format_current_observation(self, observation):
        output = []

        location = u'Current weather for {} ({})'.format(
                observation['display_location']['full'],
                observation['station_id'],
        )
        output.append(location)


        temp = u'Temperature: {} °C (Feels like: {} °C)'.format(
                observation.get('temp_c', 'N/A'),
                observation.get('feelslike_c', 'N/A')
        )
        if observation['heat_index_c'] != 'NA':
            temp += u' (Heat Index: {} °C)'.format(observation['heat_index_c'])
        if observation['windchill_c'] != 'NA':
            temp += u' (Wind Chill: {} °C)'.format(observation['windchill_c'])
        output.append(temp)


        humidity = observation.get('relative_humidity', '').strip()
        if humidity:
            output.append(u'Humidity: {}'.format(humidity))


        pressure_mb = float(observation.get('pressure_mb', 0))
        if pressure_mb:
            pressure = u'Pressure: {} kPa'.format(
                    round(pressure_mb / 10, 1)
            )
            output.append(pressure)


        condition = observation.get('weather', '').strip()
        if condition:
            output.append(u'Conditions: {}'.format(condition))


        wind_direction = observation.get('wind_dir', '').strip()
        wind_kph = observation.get('wind_kph', None)
        if wind_direction or wind_kph:
            wind = u'Wind:'
            if wind_direction:
                wind += ' {}'.format(wind_direction)
                if wind_kph:
                    wind += ' at'

            if wind_kph:
                windspeed = round(int(wind_kph) * 1000 / 3600, 2)
                wind += ' {} m/s'.format(windspeed)
            output.append(wind)


        observation_epoch = int(observation['observation_epoch'])
        updatedDiff = (datetime.now() - datetime.fromtimestamp(observation_epoch)).seconds
        if updatedDiff >= 60*60:
            updated = 'Updated: {} hours, {} mins, {} secs ago'.format(
                    (updatedDiff - (updatedDiff % (60*60))) // (60*60),
                    (updatedDiff - (updatedDiff % 60)) // 60,
                    updatedDiff % 60
            )
        elif updatedDiff >= 60:
            updated = 'Updated: {} mins, {} secs ago'.format(
                    (updatedDiff - (updatedDiff % 60)) // 60,
                    updatedDiff % 60
            )
        else:
            updated = 'Updated: {} secs ago'.format(updatedDiff)
        output.append(updated)


        return output


Class = Wunderground


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
