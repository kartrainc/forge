// Defaults for the integration suite where no stack provides them: the auth bootstrap needs an
// issuer and an audience to start, and tests that do not authenticate never reach the issuer.
// A value the environment already sets (the compose smoke lane's) wins.
process.env.GATEKEEPER_ISSUER ??= "http://gatekeeper.invalid";
process.env.SERVICE_AUDIENCE ??= "forge-services";
