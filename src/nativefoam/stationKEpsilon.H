/*---------------------------------------------------------------------------*\
  =========                 |
  \\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox
   \\    /   O peration     | Website:  https://openfoam.org
    \\  /    A nd           | Copyright (C) 2011-2020 OpenFOAM Foundation
     \\/     M anipulation  |
-------------------------------------------------------------------------------
License
    This file is part of OpenFOAM.

    OpenFOAM is free software: you can redistribute it and/or modify it
    under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    OpenFOAM is distributed in the hope that it will be useful, but WITHOUT
    ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
    FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
    for more details.

    You should have received a copy of the GNU General Public License
    along with OpenFOAM.  If not, see <http://www.gnu.org/licenses/>.

\*---------------------------------------------------------------------------*/

#ifndef stationKEpsilon_H
#define stationKEpsilon_H
#include "kEpsilon.H"
namespace Foam
{
namespace RASModels
{
template<class BasicMomentumTransportModel>
class stationKEpsilon : public kEpsilon<BasicMomentumTransportModel>
{
public:
    typedef typename BasicMomentumTransportModel::alphaField alphaField;
    typedef typename BasicMomentumTransportModel::rhoField rhoField;
    typedef typename BasicMomentumTransportModel::transportModel transportModel;
    TypeName("stationKEpsilon");
    stationKEpsilon
    (
        const alphaField& alpha, const rhoField& rho,
        const volVectorField& U, const surfaceScalarField& alphaRhoPhi,
        const surfaceScalarField& phi, const transportModel& transport,
        const word& type = typeName
    )
    : kEpsilon<BasicMomentumTransportModel>
      (alpha, rho, U, alphaRhoPhi, phi, transport, type)
    {
        this->printCoeffs(type);
    }
    virtual void correct();
};
}
}
#ifdef NoRepository
#include "stationKEpsilon.C"
#endif
#endif
