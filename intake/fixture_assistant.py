"""Fixed local test adapter. No model client, tool executor, templating or I/O."""
from copy import deepcopy

from .policy import Invalid


def evaluate(request, fixture):
    if request['mode'] != 'offline_fixture' or fixture['mode'] != 'offline_fixture':
        raise Invalid('Only offline fixtures are supported.')
    if fixture['outcome'] == 'timeout':
        raise TimeoutError('Simulated fixture timeout')
    if fixture['outcome'] == 'malformed':
        return {'invalid_test_output': True}
    # Do not parse conversation bodies for instructions or output metadata. Only
    # the explicitly imported operator fixture supplies this canned response.
    return {'format': 'intake-assistance-result-v1', 'mode': 'offline_fixture',
            'run_id': request['run_id'], 'request_token': request['request_token'],
            'ticket_id': request['input']['ticket']['id'], 'input_digest': request['input_digest'],
            'fixture_digest': request['fixture_digest'], 'source_message_id': request['input']['source_message_id'],
            'response': deepcopy(fixture['response'])}
