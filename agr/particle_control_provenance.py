"""Strict acknowledgement of external, authored and missing particle inputs."""


def confirmed_points(row, field, expected):
    points = row.get(field)
    return isinstance(points, list) and all(type(index) is int for index in points) and points == expected


def confirmed_materialized_points(row, expected):
    policy = row.get('control_point_input_policy')
    if policy is None:
        return confirmed_points(row, 'materialized_control_point_indices', expected)
    authored = row.get('authored_control_point_indices')
    if (policy not in ('agr_set_child_position_inputs_v1', 'agr_authored_child_controls_v2')
            or not confirmed_points(row, 'declared_control_point_inputs', expected)
            or not confirmed_points(row, 'unprovided_control_point_indices', [])
            or not isinstance(authored, list)
            or any(type(index) is not int or not 0 <= index <= 63 for index in authored)
            or authored != sorted(set(authored)) or set(authored).intersection(expected)):
        return False
    return confirmed_points(row, 'materialized_control_point_indices', sorted(expected + authored))
