# OCPI Tokens

A small service application that can connect to a CPMS ( charge point management system ) using the open source standard, OCPI.

https://github.com/ocpi/ocpi

The service is not a fully fledged CPMS: it stores a list of RFID cards and validates Authentication messages that are sent to it as though it were an external CPO. The ideal integration pipeline looks like this: 

* Primary CPMS receives an Authorization request from a connected charger
* The RFID of the request is not found in the Primary CPMS
* The Auh=thorization request is passed in real-time to OCPI connected partners, which includes this service
* The service returns an Accepted response if the RFID is found in it's database. 

The service ONLY supports the basic OCPI modules neccesary to make this happen. The service MUST comply with OCPI standards at all times. 

Tech stack is a Python application server, running Fast API, serving a HTML page which contains Alpine.js for reactivity. 

The service includes a basic Postgres DB, and includes a CRUD interface for RFID tokens via the admin, and API. The HTML frontend dogfoods the external API. Expected volume of tokens is between 50K to half a million, so some basic filters, pagination and categorisations is a good idea. However due to the simplicity of the data, very high volume API calls are also a viable solution. 

Authenication is handled globally via URL MS Entra, no no need for route level security on the app side. 

Project goal is to be as lightweight and simple as possible: this is a POC to see if this approach to authentication is even possible. 