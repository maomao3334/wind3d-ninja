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

Class
    Foam::fv::stationNudging

Description
    Adds a station restoring source as Su - fvm::Sp(A, psi), where A is
    minus the nonpositive implicit coefficient in the sources dictionary.
    Each selected cell receives the explicit target term and implicit sink.
    Separate options remain additive, including overlapping selections.

SourceFiles
    stationNudging.C

\*---------------------------------------------------------------------------*/

#ifndef stationNudging_H
#define stationNudging_H

#include "cellSetOption.H"
#include "Function1.T.H"
#include "objectFunction1.H"

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

namespace Foam
{
namespace fv
{

/*---------------------------------------------------------------------------*\
                     Class stationNudging Declaration
\*---------------------------------------------------------------------------*/

class stationNudging
:
    public cellSetOption
{
public:

    // Public data

        //- Enumeration for volume types
        enum class volumeMode
        {
            absolute,
            specific
        };

        //- Property type names
        static const NamedEnum<volumeMode, 2> volumeModeNames_;


private:

    // Private member data

        //- Volume mode
        volumeMode volumeMode_;

        //- Volume normalisation
        scalar VDash_;

        //- Explicit parts of the sources
        PtrList<objectFunction1> fieldSu_;

        //- Implicit parts of the sources
        PtrList<Function1<scalar>> fieldSp_;


    // Private Member Functions

        //- Add divergence terms to an equation
        template <class Type>
        void addSupType(fvMatrix<Type>&, const label);

        //- Add divergence terms to an equation
        template <class Type>
        void addSupType(const volScalarField&, fvMatrix<Type>&, const label);


public:

    //- Runtime type information
    TypeName("stationNudging");


    // Constructors

        //- Construct from components
        stationNudging
        (
            const word& name,
            const word& modelType,
            const dictionary& dict,
            const fvMesh& mesh
        );

        //- Destructor
        virtual ~stationNudging();


    // Member Functions

        // Evaluation

            // Explicit and implicit sources

                virtual void addSup
                (
                    fvMatrix<scalar>& eqn,
                    const label fieldi
                );

                virtual void addSup
                (
                    fvMatrix<vector>& eqn,
                    const label fieldi
                );

                virtual void addSup
                (
                    fvMatrix<symmTensor>& eqn,
                    const label fieldi
                );

                virtual void addSup
                (
                    fvMatrix<sphericalTensor>& eqn,
                    const label fieldi
                );

                virtual void addSup
                (
                    fvMatrix<tensor>& eqn,
                    const label fieldi
                );


            // Explicit and implicit sources for compressible equations

                virtual void addSup
                (
                    const volScalarField& rho,
                    fvMatrix<scalar>& eqn,
                    const label fieldi
                );

                virtual void addSup
                (
                    const volScalarField& rho,
                    fvMatrix<vector>& eqn,
                    const label fieldi
                );

                virtual void addSup
                (
                    const volScalarField& rho,
                    fvMatrix<symmTensor>& eqn,
                    const label fieldi
                );

                virtual void addSup
                (
                    const volScalarField& rho,
                    fvMatrix<sphericalTensor>& eqn,
                    const label fieldi
                );

                virtual void addSup
                (
                    const volScalarField& rho,
                    fvMatrix<tensor>& eqn,
                    const label fieldi
                );


            // Explicit and implicit sources for phase equations

                virtual void addSup
                (
                    const volScalarField& alpha,
                    const volScalarField& rho,
                    fvMatrix<scalar>& eqn,
                    const label fieldi
                );

                virtual void addSup
                (
                    const volScalarField& alpha,
                    const volScalarField& rho,
                    fvMatrix<vector>& eqn,
                    const label fieldi
                );

                virtual void addSup
                (
                    const volScalarField& alpha,
                    const volScalarField& rho,
                    fvMatrix<symmTensor>& eqn,
                    const label fieldi
                );

                virtual void addSup
                (
                    const volScalarField& alpha,
                    const volScalarField& rho,
                    fvMatrix<sphericalTensor>& eqn,
                    const label fieldi
                );

                virtual void addSup
                (
                    const volScalarField& alpha,
                    const volScalarField& rho,
                    fvMatrix<tensor>& eqn,
                    const label fieldi
                );


        // IO

            //- Read source dictionary
            virtual bool read(const dictionary& dict);
};


// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

} // End namespace fv
} // End namespace Foam


// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

#endif

// ************************************************************************* //
