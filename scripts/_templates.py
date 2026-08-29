"""Shared SUMO config template for build_network.py and build_showcase.py."""

SUMOCFG_XML = """<configuration>
    <input>
        <net-file value="{net}"/>
        <route-files value="{routes}"/>
        <additional-files value="vtypes.add.xml"/>
    </input>
    <time>
        <step-length value="0.5"/>
    </time>
    <processing>
        <ignore-route-errors value="true"/>
        <time-to-teleport value="180"/>
    </processing>
    <report>
        <no-step-log value="true"/>
        <verbose value="false"/>
    </report>
</configuration>
"""
