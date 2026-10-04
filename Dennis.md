
There sure is:  /v2/signups?extra_fields[signups]=registered_address will return all signups and include a registered_address object, which contains zip and state fields. This returns all signups (using pagination), or /v2/signups/{signup_id} works for specific records. 
