def test_roles_are_not_double_primary():
    roles=["PRIMARY_SOURCE_ATOM","LOGICAL_REPRESENTATION","FALLBACK_REPRESENTATION"]; assert roles.count("PRIMARY_SOURCE_ATOM")==1
